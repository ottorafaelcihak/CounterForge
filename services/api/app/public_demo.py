"""Private snapshot preparation and fail-closed public demo reads.

Preparation binds a sanitized public manifest to retained evidence hashes.
Nothing is public until an operator explicitly publishes a prepared
snapshot; every public read re-verifies the binding and fails closed to an
empty manifest when source evidence is stale, missing or tampered.
"""

import hashlib
from typing import Any
from uuid import UUID, uuid4

from counterforge_contracts.requests import DemoPublicationRequest, DemoSnapshotPrepareRequest
from counterforge_contracts.responses import (
    DemoManifest,
    DemoSnapshot,
    PublicDemoMetrics,
    ValidationReport,
)
from counterforge_contracts.versions import ErrorCode
from counterforge_verification.invariants import report_digest
from sqlalchemy import func, select

from .artifacts import IntegrityError, normalize_image
from .errors import ServiceError
from .service import Service, canonical_json, fingerprint

ARCHITECTURE = [
    "Recorded experiment evidence is validated against a locked visual test contract before any finding is eligible for the demo.",
    "A human reviewer approves individual stable findings; only approved findings can enter a prepared snapshot.",
    "The public snapshot is a sanitized, hash-bound copy: fresh public asset IDs, stored counts and limitations, with no private identifiers or controls.",
    "This page is read-only replay. It cannot start experiments, call models or download unapproved artifacts.",
]

LIMITATIONS = [
    "Findings describe the recorded settings and sample only; they are not a claim of universal model behaviour.",
    "Counts are stored run totals, except approved findings which count only this snapshot. Total target calls include seed baseline and minimization retest calls; canonical screening counts exclude both.",
    "Fixture-provenance results demonstrate the pipeline and are not evidence of live foundation-model failures.",
]


def _fixture_mode(s: Service) -> bool:
    return s.settings.provider_mode == "FIXTURE" and s.settings.allow_synthetic_fixtures


def empty_manifest(s: Service) -> DemoManifest:
    fixture = _fixture_mode(s)
    limitations = list(LIMITATIONS)
    limitations.insert(
        0,
        "No approved snapshot is published yet. Snapshot preparation and "
        "publication are private operator steps, so this demo is intentionally empty."
        if not fixture
        else "LOCAL FIXTURE preview: no fixture snapshot is published yet, so this preview is intentionally empty.",
    )
    return DemoManifest(
        state="FIXTURE_PREVIEW" if fixture else "EMPTY",
        provenance="LOCAL_FIXTURE" if fixture else None,
        architecture=list(ARCHITECTURE),
        limitations=limitations,
    )


def _fail(message: str) -> ServiceError:
    return ServiceError(ErrorCode.REVIEW_REQUIRED, message)


def _verified_image(s: Service, asset_id: UUID) -> dict[str, Any]:
    asset = s.asset(asset_id)
    if asset["media_type"] not in {"image/png", "image/jpeg", "image/webp"}:
        raise _fail("Selected demo evidence is not a publishable image.")
    try:
        data = s.artifacts.read_verified(asset_id)
        decoded = normalize_image(data, asset["media_type"])
    except (IntegrityError, KeyError, ValueError) as exc:
        raise _fail("Selected demo image failed integrity verification.") from exc
    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise _fail("Selected demo image hash does not match retained evidence.")
    return {
        "source_asset_id": str(asset_id),
        "sha256": asset["sha256"],
        "media_type": asset["media_type"],
        "width": decoded.width,
        "height": decoded.height,
    }


def _source_experiment_sha(s: Service, experiment: Any) -> str:
    """Private hash binding the snapshot to its exact retained source run."""
    contract = s.contract(experiment["contract_id"])
    target = experiment["config_snapshot"].get("target") or {}
    return fingerprint(
        {
            "experiment_id": str(experiment["id"]),
            "contract_sha256": contract["contract_sha256"],
            "target_adapter": target.get("adapter_key"),
            "target_model_id": target.get("model_id"),
            "target_model_revision": target.get("model_revision"),
        }
    )


def _bound_report_sha(s: Service, finding: Any, mutation_id: UUID) -> tuple[str, ValidationReport]:
    validations = s.t.mutation_validations
    latest = (
        s.db.execute(
            select(validations)
            .where(validations.c.mutation_id == mutation_id)
            .order_by(validations.c.revision.desc(), validations.c.id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if latest is None or "report" not in (latest["evidence_json"] or {}):
        raise _fail("Finding has no sealed validation report to bind.")
    evidence = latest["evidence_json"]
    try:
        report = ValidationReport.model_validate(evidence["report"])
    except ValueError as exc:
        raise _fail("Retained validation report cannot be verified.") from exc
    if report.verdict != "VALID" or report.report_sha256 != report_digest(report):
        raise _fail("Retained validation report hash does not verify.")
    if finding["demo_validation_sha256"] != report.report_sha256:
        raise _fail("Finding demo approval is stale against the sealed report.")
    report_asset_id = evidence.get("report_asset_id")
    report_sha = evidence.get("report_asset_sha256")
    if not report_asset_id or not report_sha:
        raise _fail("Sealed validation report bytes are not retained.")
    try:
        body = s.artifacts.read_verified(UUID(str(report_asset_id)))
    except (IntegrityError, KeyError, ValueError) as exc:
        raise _fail("Sealed validation report bytes failed verification.") from exc
    if hashlib.sha256(body).hexdigest() != report_sha or body != canonical_json(
        report.model_dump(mode="json")
    ):
        raise _fail("Sealed validation report bytes do not match their hash.")
    return str(report.report_sha256), report


def _checked_counts(s: Service, finding: Any, report: ValidationReport) -> tuple[int, int]:
    """Cross-check public confirmation counts against retained observations.

    Reuses the export evidence validators read-only (zero dispatch). When
    confirmation observations are retained they are authoritative; the
    mutable finding counters alone are never trusted, including fixtures.
    """
    from .exports import eligibility, observations

    try:
        obs = observations(s, finding)
    except (IntegrityError, ServiceError, ValueError, KeyError, TypeError) as exc:
        raise _fail("Retained observation evidence failed verification.") from exc
    blocking = eligibility(s, finding, report, obs)
    if blocking:
        raise _fail("Retained observation evidence is incomplete for a demo finding.")
    fresh = [item for item in obs if item.phase == "CONFIRMATION"]
    incorrect = sum(1 for item in fresh if item.outcome == "INCORRECT")
    if (
        incorrect != finding["confirmation_failures"]
        or len(fresh) != finding["confirmation_attempts"]
    ):
        raise _fail("Finding counters do not match retained confirmation observations.")
    return incorrect, len(fresh)


def _metrics(s: Service, experiment_id: UUID, selected_findings: int) -> PublicDemoMetrics:
    mutations, validations = s.t.mutations, s.t.mutation_validations
    latest = (
        select(validations.c.mutation_id, validations.c.verdict)
        .distinct(validations.c.mutation_id)
        .order_by(validations.c.mutation_id, validations.c.revision.desc(), validations.c.id.desc())
        .subquery()
    )
    counts = {
        verdict: count
        for verdict, count in s.db.execute(
            select(latest.c.verdict, func.count())
            .select_from(mutations.join(latest, mutations.c.id == latest.c.mutation_id))
            .where(
                mutations.c.experiment_id == experiment_id,
                mutations.c.minimization_job_id.is_(None),
            )
            .group_by(latest.c.verdict)
        ).all()
    }
    generated = s.db.scalar(
        select(func.count())
        .select_from(mutations)
        .where(
            mutations.c.experiment_id == experiment_id,
            mutations.c.image_asset_id.is_not(None),
            mutations.c.minimization_job_id.is_(None),
        )
    )
    observations = s.t.model_observations
    screened = s.db.scalar(
        select(func.count())
        .select_from(observations.join(latest, observations.c.mutation_id == latest.c.mutation_id))
        .where(
            observations.c.experiment_id == experiment_id,
            observations.c.phase == "SCREENING",
            latest.c.verdict == "VALID",
        )
    )
    experiment = s.experiment(experiment_id)
    return PublicDemoMetrics(
        generated_candidates=generated or 0,
        valid_candidates=counts.get("VALID", 0),
        invalid_candidates=counts.get("INVALID", 0),
        review_required_candidates=counts.get("REVIEW_REQUIRED", 0),
        valid_screening_calls=screened or 0,
        total_target_calls=experiment["calls_consumed"],
        # Selected approved findings in this snapshot, never a whole-run
        # count that could imply unapproved findings are public.
        stable_findings=selected_findings,
    )


def _build_snapshot(
    s: Service, experiment_id: UUID, *, include_seed_image: bool = False
) -> tuple[DemoManifest, dict[str, Any], str]:
    experiment = s.experiment(experiment_id)
    contract = s.contract(experiment["contract_id"])
    seed = s.row(s.t.seed_cases, contract["seed_id"])
    target = experiment["config_snapshot"].get("target") or {}
    adapter = target.get("adapter_key")
    if adapter not in {"META_SPARK", "LOCAL_FIXTURE"} or not target.get("model_id"):
        raise _fail("Experiment target identity is not retained for a demo snapshot.")
    # A fixture-mode run is fixture provenance even when its pinned adapter
    # names a real model; synthetic runs must never read as real results.
    mode = experiment["config_snapshot"].get("provider_mode")
    if mode not in {"FIXTURE", "LIVE"}:
        raise _fail("Experiment provider provenance is unexecuted or unknown.")
    provenance = (
        "LOCAL_FIXTURE" if adapter == "LOCAL_FIXTURE" or mode == "FIXTURE" else "REAL_PROVIDER"
    )
    findings = (
        s.db.execute(
            select(s.t.findings)
            .where(
                s.t.findings.c.experiment_id == experiment_id,
                s.t.findings.c.demo_approved_at.is_not(None),
            )
            .order_by(s.t.findings.c.created_at, s.t.findings.c.id)
        )
        .mappings()
        .all()
    )
    terminal = experiment["state"] in {"COMPLETED", "BUDGET_EXHAUSTED", "CANCELLED", "FAILED"}
    if not findings and not terminal:
        raise _fail("No demo-approved findings are retained for this experiment.")
    public_findings: list[dict[str, Any]] = []
    public_assets: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    # One public asset per source in this snapshot: several findings
    # from one seed share its ORIGINAL image, and the assets table is unique
    # on (snapshot_id, source_asset_id).
    asset_cache: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for finding in findings:
        if finding["status"] != "STABLE" or finding["confirmation_attempts"] < 1:
            raise _fail("Only stable, confirmed findings may enter a demo snapshot.")
        mutation = s.row(s.t.mutations, finding["mutation_id"])
        if mutation["image_asset_id"] is None:
            raise _fail("Finding image evidence is incomplete.")
        report_sha, report = _bound_report_sha(s, finding, finding["mutation_id"])
        incorrect, attempts = _checked_counts(s, finding, report)
        public_finding_id = uuid4()
        asset_ids: list[str] = []
        finding_bindings: list[dict[str, Any]] = []
        for role, source_id in (
            ("ORIGINAL", seed["image_asset_id"]),
            ("MUTATED", mutation["image_asset_id"]),
        ):
            cache_key = str(source_id)
            if cache_key not in asset_cache:
                image = _verified_image(s, source_id)
                public_asset_id = uuid4()
                asset_cache[cache_key] = (
                    str(public_asset_id),
                    {
                        "id": str(public_asset_id),
                        "role": role,
                        "sha256": image["sha256"],
                        "media_type": image["media_type"],
                        "width": image["width"],
                        "height": image["height"],
                        "content_url": f"/api/v1/public/demo/assets/{public_asset_id}/content",
                    },
                    image,
                )
                public_assets.append(asset_cache[cache_key][1])
            public_asset_id_str, public_asset, image = asset_cache[cache_key]
            if public_asset["role"] != role:
                raise _fail("A source image cannot alias multiple public roles in one snapshot.")
            asset_ids.append(public_asset_id_str)
            finding_bindings.append({"public_asset_id": public_asset_id_str, "role": role, **image})
        public_findings.append(
            {
                "id": str(public_finding_id),
                "status": "STABLE",
                "ground_truth_count": contract["contract_json"]["expected"]["value"],
                "model_adapter": adapter,
                "model_id": target["model_id"],
                "mutation_family": mutation["family"],
                "mutation_strength": float(mutation["strength"]),
                "confirmation_incorrect": incorrect,
                "confirmation_attempts": attempts,
                "qualification": (
                    f"{incorrect}/{attempts} "
                    "incorrect under the recorded settings; no claim of universal reproducibility."
                ),
                "asset_ids": asset_ids,
            }
        )
        bindings.append(
            {
                "private_finding_id": str(finding["id"]),
                "public_finding_id": str(public_finding_id),
                "report_sha256": report_sha,
                "assets": finding_bindings,
            }
        )
    limitations = list(LIMITATIONS)
    if not findings:
        limitations.insert(
            0,
            "No confirmed finding in this recorded sample; the run is shown without inventing one.",
        )
        if include_seed_image:
            image = _verified_image(s, seed["image_asset_id"])
            public_asset_id = uuid4()
            public_assets.append(
                {
                    "id": str(public_asset_id),
                    "role": "ORIGINAL",
                    "sha256": image["sha256"],
                    "media_type": image["media_type"],
                    "width": image["width"],
                    "height": image["height"],
                    "content_url": f"/api/v1/public/demo/assets/{public_asset_id}/content",
                }
            )
            bindings.append(
                {
                    "private_finding_id": str(uuid4()),
                    "public_finding_id": str(uuid4()),
                    "report_sha256": "0" * 64,
                    "seed_only": True,
                    "assets": [
                        {"public_asset_id": str(public_asset_id), "role": "ORIGINAL", **image}
                    ],
                }
            )
    manifest = DemoManifest(
        state="PUBLISHED",
        provenance=provenance,
        findings=public_findings,
        assets=public_assets,
        metrics=_metrics(s, experiment_id, len(public_findings)),
        architecture=list(ARCHITECTURE),
        limitations=limitations,
    )
    payload = manifest.model_dump(mode="json")
    return (
        manifest,
        {"findings": bindings, "source_experiment_sha256": _source_experiment_sha(s, experiment)},
        fingerprint(payload),
    )


def _admin(s: Service, row: Any) -> DemoSnapshot:
    bindings = row["private_bindings_json"].get("findings", [])
    assets = s.db.scalar(
        select(func.count())
        .select_from(s.t.public_demo_assets)
        .where(s.t.public_demo_assets.c.snapshot_id == row["id"])
    )
    return DemoSnapshot(
        id=row["id"],
        status=row["status"],
        provenance=row["provenance"],
        source_experiment_id=row["source_experiment_id"],
        source_experiment_sha256=row["private_bindings_json"].get(
            "source_experiment_sha256", "0" * 64
        ),
        manifest_sha256=row["manifest_sha256"],
        source_report_sha256=sorted({item["report_sha256"] for item in bindings} - {"0" * 64}),
        finding_count=len([item for item in bindings if not item.get("seed_only")]),
        asset_count=assets or 0,
        prepared_by=row["prepared_by"],
        prepared_at=row["prepared_at"],
        published_by=row["published_by"],
        published_at=row["published_at"],
        publication_intent=row["publication_intent"],
    )


def prepare_snapshot(
    s: Service, experiment_id: UUID, body: DemoSnapshotPrepareRequest | None = None
) -> DemoSnapshot:
    experiment = s.experiment(experiment_id)
    manifest, bindings, digest = _build_snapshot(
        s, experiment_id, include_seed_image=bool(body and body.include_seed_image)
    )
    snapshot_id = uuid4()
    payload = manifest.model_dump(mode="json")
    s.db.execute(
        s.t.public_demo_snapshots.insert().values(
            id=snapshot_id,
            source_experiment_id=experiment_id,
            source_project_id=experiment["project_id"],
            status="DRAFT",
            provenance=manifest.provenance,
            manifest_json=payload,
            manifest_sha256=digest,
            private_bindings_json=bindings,
            prepared_by=s.settings.owner_label,
        )
    )
    inserted_assets: set[str] = set()
    for finding in bindings["findings"]:
        for asset in finding["assets"]:
            if asset["public_asset_id"] in inserted_assets:
                continue  # shared seed image across findings: one public row
            inserted_assets.add(asset["public_asset_id"])
            s.db.execute(
                s.t.public_demo_assets.insert().values(
                    id=UUID(asset["public_asset_id"]),
                    snapshot_id=snapshot_id,
                    source_asset_id=UUID(asset["source_asset_id"]),
                    public_finding_id=UUID(finding["public_finding_id"]),
                    role=asset["role"],
                    sha256=asset["sha256"],
                    media_type=asset["media_type"],
                    width=asset["width"],
                    height=asset["height"],
                )
            )
    s.audit(
        experiment["project_id"],
        "DEMO_SNAPSHOT_PREPARED",
        "public_demo_snapshot",
        snapshot_id,
        manifest_sha256=digest,
        provenance=manifest.provenance,
    )
    return _admin(s, s.row(s.t.public_demo_snapshots, snapshot_id))


def _verify_bindings(s: Service, row: Any) -> None:
    if fingerprint(row["manifest_json"]) != row["manifest_sha256"]:
        raise _fail("Snapshot manifest hash does not verify.")
    experiment = s.experiment(row["source_experiment_id"])
    mode = experiment["config_snapshot"].get("provider_mode")
    adapter = (experiment["config_snapshot"].get("target") or {}).get("adapter_key")
    provenance = (
        "LOCAL_FIXTURE" if mode == "FIXTURE" or adapter == "LOCAL_FIXTURE" else "REAL_PROVIDER"
    )
    if mode not in {"LIVE", "FIXTURE"} or provenance != row["provenance"]:
        raise _fail("Snapshot provider provenance is unexecuted or unknown.")
    if experiment["project_id"] != row["source_project_id"]:
        raise _fail("Snapshot source experiment no longer matches its binding.")
    if _source_experiment_sha(s, experiment) != row["private_bindings_json"].get(
        "source_experiment_sha256"
    ):
        raise _fail("Snapshot source experiment hash does not verify.")
    for finding_binding in row["private_bindings_json"].get("findings", []):
        if finding_binding.get("seed_only"):
            for asset_binding in finding_binding["assets"]:
                public_asset = s.row(s.t.public_demo_assets, UUID(asset_binding["public_asset_id"]))
                if (
                    public_asset["snapshot_id"] != row["id"]
                    or str(public_asset["source_asset_id"]) != asset_binding["source_asset_id"]
                    or public_asset["sha256"] != asset_binding["sha256"]
                ):
                    raise _fail("Snapshot asset binding is stale.")
                if (
                    _verified_image(s, UUID(asset_binding["source_asset_id"]))["sha256"]
                    != asset_binding["sha256"]
                ):
                    raise _fail("Snapshot image hash does not verify.")
            continue
        finding = s.row(s.t.findings, UUID(finding_binding["private_finding_id"]))
        if finding["demo_approved_at"] is None or finding["status"] != "STABLE":
            raise _fail("Snapshot finding approval is no longer current.")
        report_sha, report = _bound_report_sha(s, finding, finding["mutation_id"])
        _checked_counts(s, finding, report)
        if report_sha != finding_binding["report_sha256"]:
            raise _fail("Snapshot report binding is stale.")
        for asset_binding in finding_binding["assets"]:
            public_asset = s.row(s.t.public_demo_assets, UUID(asset_binding["public_asset_id"]))
            if (
                public_asset["snapshot_id"] != row["id"]
                or str(public_asset["source_asset_id"]) != asset_binding["source_asset_id"]
                or public_asset["sha256"] != asset_binding["sha256"]
            ):
                raise _fail("Snapshot asset binding is stale.")
            image = _verified_image(s, UUID(asset_binding["source_asset_id"]))
            if image["sha256"] != asset_binding["sha256"]:
                raise _fail("Snapshot image hash does not verify.")


PUBLISH_LOCK = 73402099


def _owned_snapshot(s: Service, snapshot_id: UUID, *, lock: bool = False) -> Any:
    row = s.row(s.t.public_demo_snapshots, snapshot_id, lock=lock)
    # Owner scope is the source project, consistent across admin paths.
    s.project(row["source_project_id"])
    return row


def publish_snapshot(s: Service, snapshot_id: UUID, body: DemoPublicationRequest) -> DemoSnapshot:
    # Serialize publication so superseding the prior snapshot is atomic;
    # the unique partial index on PUBLISHED is the database backstop.
    s.db.execute(select(func.pg_advisory_xact_lock(PUBLISH_LOCK)))
    row = _owned_snapshot(s, snapshot_id, lock=True)
    if row["status"] not in {"DRAFT", "REVOKED"}:
        raise ServiceError(ErrorCode.INVALID_STATE, "Only a prepared snapshot can be published.")
    if body.manifest_sha256 != row["manifest_sha256"]:
        raise ServiceError(
            ErrorCode.INVALID_STATE,
            "Publication must confirm the exact prepared snapshot hash.",
        )
    _verify_bindings(s, row)
    s.db.execute(
        s.t.public_demo_snapshots.update()
        .where(
            s.t.public_demo_snapshots.c.status == "PUBLISHED",
            s.t.public_demo_snapshots.c.id != snapshot_id,
        )
        .values(status="SUPERSEDED")
    )
    s.db.execute(
        s.t.public_demo_snapshots.update()
        .where(s.t.public_demo_snapshots.c.id == snapshot_id)
        .values(
            status="PUBLISHED",
            published_by=s.settings.owner_label,
            published_at=func.now(),
            publication_intent=body.publication_intent,
            revoked_at=None,
        )
    )
    s.audit(
        row["source_project_id"],
        "DEMO_SNAPSHOT_PUBLISHED",
        "public_demo_snapshot",
        snapshot_id,
        manifest_sha256=row["manifest_sha256"],
        publication_intent_sha256=fingerprint(body.publication_intent),
        redistribution_rights_acknowledged=True,
    )
    return _admin(s, s.row(s.t.public_demo_snapshots, snapshot_id))


def revoke_snapshot(s: Service, snapshot_id: UUID) -> DemoSnapshot:
    row = _owned_snapshot(s, snapshot_id, lock=True)
    if row["status"] in {"PUBLISHED", "DRAFT"}:
        s.db.execute(
            s.t.public_demo_snapshots.update()
            .where(s.t.public_demo_snapshots.c.id == snapshot_id)
            .values(status="REVOKED", revoked_at=func.now())
        )
        s.audit(
            row["source_project_id"],
            "DEMO_SNAPSHOT_REVOKED",
            "public_demo_snapshot",
            snapshot_id,
            manifest_sha256=row["manifest_sha256"],
        )
    return _admin(s, s.row(s.t.public_demo_snapshots, snapshot_id))


def get_snapshot(s: Service, snapshot_id: UUID) -> DemoSnapshot:
    return _admin(s, _owned_snapshot(s, snapshot_id))


def _active_snapshot(s: Service) -> Any | None:
    snapshots = s.t.public_demo_snapshots
    return (
        s.db.execute(
            select(snapshots)
            .where(snapshots.c.status == "PUBLISHED")
            .order_by(snapshots.c.published_at.desc(), snapshots.c.id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )


def public_manifest(s: Service) -> DemoManifest:
    row = _active_snapshot(s)
    if row is None:
        return empty_manifest(s)
    if row["provenance"] == "LOCAL_FIXTURE" and not _fixture_mode(s):
        return empty_manifest(s)
    try:
        _verify_bindings(s, row)
        manifest = DemoManifest.model_validate(row["manifest_json"])
    except (ServiceError, ValueError):
        return empty_manifest(s)
    return manifest.model_copy(
        update={
            "state": "FIXTURE_PREVIEW" if row["provenance"] == "LOCAL_FIXTURE" else "PUBLISHED",
            "snapshot_id": row["id"],
            "published_at": row["published_at"],
        }
    )


def public_asset(s: Service, public_asset_id: UUID) -> tuple[bytes, str]:
    def denied() -> ServiceError:
        return ServiceError(ErrorCode.NOT_FOUND, "Public asset was not found.", 404)

    assets, snapshots = s.t.public_demo_assets, s.t.public_demo_snapshots
    joined = (
        s.db.execute(
            select(assets, snapshots.c.status, snapshots.c.provenance)
            .join(snapshots, assets.c.snapshot_id == snapshots.c.id)
            .where(assets.c.id == public_asset_id)
        )
        .mappings()
        .one_or_none()
    )
    if joined is None or joined["status"] != "PUBLISHED":
        raise denied()
    snapshot = s.row(snapshots, joined["snapshot_id"])
    if snapshot["provenance"] == "LOCAL_FIXTURE" and not _fixture_mode(s):
        raise denied()
    active = _active_snapshot(s)
    if active is None or active["id"] != snapshot["id"]:
        raise denied()  # only the exact currently published snapshot serves
    try:
        # Full binding verification for every path, including seed-only
        # images: source experiment, reports, approvals and image hashes.
        _verify_bindings(s, snapshot)
    except Exception as exc:
        raise denied() from exc
    bound = None
    bound_finding = None
    for finding_binding in snapshot["private_bindings_json"].get("findings", []):
        for asset_binding in finding_binding["assets"]:
            if asset_binding["public_asset_id"] == str(public_asset_id):
                bound, bound_finding = asset_binding, finding_binding
    if bound is None or bound_finding is None or bound["sha256"] != joined["sha256"]:
        raise denied()
    if bound_finding.get("seed_only"):
        try:
            data = s.artifacts.read_verified(joined["source_asset_id"])
        except (IntegrityError, KeyError) as exc:
            raise denied() from exc
        if hashlib.sha256(data).hexdigest() != joined["sha256"]:
            raise denied()
        return data, joined["media_type"]
    try:
        finding = s.row(s.t.findings, UUID(bound_finding["private_finding_id"]))
        if finding["demo_approved_at"] is None or finding["status"] != "STABLE":
            raise denied()
        report_sha, report = _bound_report_sha(s, finding, finding["mutation_id"])
        _checked_counts(s, finding, report)
        if report_sha != bound_finding["report_sha256"]:
            raise denied()
    except ServiceError as exc:
        if exc.status == 404:
            raise
        raise denied() from exc
    try:
        data = s.artifacts.read_verified(joined["source_asset_id"])
    except (IntegrityError, KeyError) as exc:
        raise denied() from exc
    if hashlib.sha256(data).hexdigest() != joined["sha256"]:
        raise denied()
    return data, joined["media_type"]
