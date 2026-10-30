"""Current, sealed, owner-scoped export evidence. No provider dispatch or arbitrary DTOs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from counterforge_contracts.responses import (
    AssetLink,
    FindingEvidence,
    FindingObservation,
    FindingUsage,
    FindingValidationRevision,
    ValidationReport,
)
from counterforge_contracts.versions import ErrorCode
from counterforge_regression.bundle import canonical, scan
from counterforge_regression.manifest import RegressionManifest
from counterforge_targets.meta_spark import decode_reply
from sqlalchemy import select

from .artifacts import EXTENSIONS, IntegrityError
from .errors import ServiceError
from .redaction import review_text

if TYPE_CHECKING:
    from .service import Service


def clean(value: str, forbidden_values: tuple[bytes, ...] = ()) -> str:
    # Public retained text is sanitized, never the whole provider envelope.
    text = review_text(value)
    scan(text.encode(), forbidden_values)
    return text


def safe_report(
    report: ValidationReport, forbidden_values: tuple[bytes, ...] = ()
) -> ValidationReport:
    # Fail rather than silently alter the sealed report/hash binding.
    scan(canonical(report.model_dump(mode="json")), forbidden_values)
    return report


def observations(s: Service, finding: Any) -> list[FindingObservation]:
    from services.worker.app.experiments import target_invocation

    from .service import fingerprint

    e = s.experiment(finding["experiment_id"])
    snap = e["config_snapshot"]
    target = snap["target"]
    expected = snap["contract"]["expected"]["value"]
    provenance: Literal["LOCAL_FIXTURE", "REAL_PROVIDER"] = (
        "LOCAL_FIXTURE"
        if snap.get("provider_mode") == "FIXTURE" or target["adapter_key"] == "LOCAL_FIXTURE"
        else "REAL_PROVIDER"
    )
    rows = (
        s.db.execute(
            select(s.t.model_observations)
            .where(
                s.t.model_observations.c.experiment_id == e["id"],
                s.t.model_observations.c.retest_job_id.is_(None),
                (s.t.model_observations.c.mutation_id == finding["mutation_id"])
                | (
                    (s.t.model_observations.c.phase == "SEED_BASELINE")
                    & (s.t.model_observations.c.seed_id == UUID(snap["seed"]["id"]))
                ),
            )
            .order_by(s.t.model_observations.c.created_at, s.t.model_observations.c.id)
        )
        .mappings()
        .all()
    )
    result = []
    secrets = configured_secrets(s.settings)
    for row in rows:
        image_id = (
            UUID(snap["seed"]["image_asset_id"])
            if row["phase"] == "SEED_BASELINE"
            else s.candidates.row(finding["mutation_id"])["image_asset_id"]
        )
        asset = s.asset(image_id)
        if (
            asset["project_id"] != e["project_id"]
            or row["target_config_id"] != e["target_config_id"]
        ):
            raise IntegrityError("Observation ownership mismatch")
        image = s.artifacts.read_verified(image_id)
        invocation = target_invocation(snap, image_id, image)
        if row["invocation_json"] != invocation or row["request_fingerprint"] != fingerprint(
            invocation
        ):
            raise IntegrityError("Observation invocation binding failed")
        usage = (
            s.db.execute(
                select(s.t.usage_events).where(
                    s.t.usage_events.c.operation_key == row["operation_key"]
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            usage is None
            or usage["experiment_id"] != e["id"]
            or usage["status"] != "SETTLED"
            or usage["dispatched_at"] is None
            or usage["outcome"] != "COMPLETED"
            or usage["raw_response_asset_id"] != row["raw_output_asset_id"]
            or usage["metadata_json"].get("invocation_sha256") != fingerprint(invocation)
        ):
            raise IntegrityError("Observation accounting/raw evidence binding failed")
        raw_asset = s.asset(row["raw_output_asset_id"])
        if raw_asset["project_id"] != e["project_id"]:
            raise IntegrityError("Raw evidence ownership mismatch")
        wire = json.loads(s.artifacts.read_verified(raw_asset["id"]))
        reply = wire.get("response", wire)
        raw, parsed, outcome = decode_reply(reply, usage["outcome"], expected)
        if row["parsed_json"] != {"count": parsed} or row["outcome"] != outcome:
            raise IntegrityError("Observation parsed evidence differs from retained response")
        if (
            row["phase"] == "CONFIRMATION"
            and row["confirmation_job_id"] != finding["confirmation_job_id"]
        ):
            raise IntegrityError("Observation does not belong to canonical confirmation")
        options = dict(target["inference_options"])
        if target["adapter_key"] == "META_SPARK":
            token_key = (
                "max_output_tokens" if "max_output_tokens" in options else "max_completion_tokens"
            )
            options[token_key] = options.get(token_key, 128)
        if row["sampling_config"] != options:
            raise IntegrityError("Observation sampling settings mismatch")
        scan(canonical(options), secrets)
        result.append(
            FindingObservation(
                id=row["id"],
                phase=row["phase"],
                attempt_no=row["attempt_no"],
                raw_text=clean(raw, secrets),
                parsed_count=parsed,
                outcome=outcome,
                image_sha256=asset["sha256"],
                prompt=clean(invocation["prompt"], secrets),
                sampling_options=options,
                model_id=clean(target["model_id"], configured_secrets(s.settings)),
                model_revision=clean(
                    row["model_version_observed"] or target["model_revision"], secrets
                ),
                provenance=provenance,
                latency_ms=row["latency_ms"],
                recorded_at=row["created_at"],
            )
        )
    return result


def eligibility(
    s: Service, f: Any, report: ValidationReport, obs: list[FindingObservation]
) -> list[str]:
    reasons = []
    if (
        f["status"] != "STABLE"
        or f["confirmation_attempts"] != 3
        or f["confirmation_failures"] != 3
    ):
        reasons.append("STABLE_THREE_FRESH_REQUIRED")
    if report.verdict != "VALID":
        reasons.append("CURRENT_VALID_REQUIRED")
    if not report.review_audit:
        reasons.append("EVIDENCE_BOUND_HUMAN_REVIEW_REQUIRED")
    baseline = [x for x in obs if x.phase == "SEED_BASELINE"]
    screen = [x for x in obs if x.phase == "SCREENING"]
    fresh = [x for x in obs if x.phase == "CONFIRMATION"]
    if (
        len(baseline) != 3
        or {x.attempt_no for x in baseline} != {1, 2, 3}
        or any(x.outcome != "CORRECT" for x in baseline)
    ):
        reasons.append("CORRECT_BASELINE_REQUIRED")
    if len(screen) != 1 or screen[0].outcome != "INCORRECT":
        reasons.append("SEPARATE_INCORRECT_SCREEN_REQUIRED")
    if (
        len(fresh) != 3
        or {x.attempt_no for x in fresh} != {1, 2, 3}
        or any(x.outcome != "INCORRECT" for x in fresh)
    ):
        reasons.append("COMPLETE_CANONICAL_CONFIRMATION_REQUIRED")
    if not f["confirmation_job_id"]:
        reasons.append("CANONICAL_JOB_REQUIRED")
    else:
        job = s.job(f["confirmation_job_id"])
        if (
            job["kind"] != "CONFIRM"
            or job["state"] != "SUCCEEDED"
            or job["finding_id"] != f["id"]
            or job["experiment_id"] != f["experiment_id"]
        ):
            reasons.append("CANONICAL_JOB_INCOMPLETE")
    return reasons


def finding_evidence(s: Service, finding_id: UUID) -> FindingEvidence:
    f = s.finding(finding_id)
    e = s.experiment(f["experiment_id"])
    m = s.candidates.row(f["mutation_id"])
    try:
        report = safe_report(s.candidates.checked_report(m), configured_secrets(s.settings))
        obs = observations(s, f)
        latest = s.candidates.latest(m["id"])
        assets = dict(m["evidence_json"]["assets"])
        assets["protected_mask"] = assets.pop("protected_union")
        assets["report"] = latest["evidence_json"]["report_asset_id"]
        links = {}
        for name, aid in assets.items():
            asset = s.asset(UUID(aid))
            if asset["project_id"] != e["project_id"]:
                raise IntegrityError("Asset ownership mismatch")
            s.artifacts.read_verified(asset["id"])
            links[name] = AssetLink(
                asset_id=asset["id"],
                sha256=asset["sha256"],
                content_url=f"/api/v1/assets/{asset['id']}/content",
            )
        history = []
        for v in s.db.execute(
            select(s.t.mutation_validations)
            .where(s.t.mutation_validations.c.mutation_id == m["id"])
            .order_by(s.t.mutation_validations.c.revision)
        ).mappings():
            if "report" in v["evidence_json"]:
                history.append(
                    FindingValidationRevision(
                        revision=v["revision"],
                        report=safe_report(
                            ValidationReport.model_validate(v["evidence_json"]["report"]),
                            configured_secrets(s.settings),
                        ),
                        created_at=v["created_at"],
                    )
                )
        usage = (
            s.db.execute(
                select(s.t.usage_events).where(s.t.usage_events.c.experiment_id == e["id"])
            )
            .mappings()
            .all()
        )
        dispatched = [u for u in usage if u["dispatched_at"] is not None]
        totals = FindingUsage(
            calls=sum(u["request_count"] for u in dispatched),
            target_calls=sum(u["request_count"] for u in dispatched if u["category"] == "TARGET"),
            known_tokens_in=sum(u["tokens_in"] or 0 for u in dispatched),
            known_tokens_out=sum(u["tokens_out"] or 0 for u in dispatched),
            unknown_token_calls=sum(
                u["request_count"]
                for u in dispatched
                if u["tokens_in"] is None or u["tokens_out"] is None
            ),
            actual_usd=float(
                sum(
                    (u["actual_usd"] or 0)
                    for u in usage
                    if u["status"] == "SETTLED" and u["cost_kind"] == "ACTUAL"
                )
            ),
            estimated_usd=float(
                sum(
                    u["accounted_estimate_usd"]
                    for u in usage
                    if u["status"] == "SETTLED" and u["cost_kind"] == "ESTIMATED"
                )
            ),
            reserved_usd=float(
                sum(u["estimated_max_usd"] for u in usage if u["status"] == "RESERVED")
            ),
            unresolved_usd=float(
                sum(u["estimated_max_usd"] for u in usage if u["status"] == "UNKNOWN_CHARGE")
            ),
        )
        reasons = eligibility(s, f, report, obs)
        provenance = (
            "LOCAL_FIXTURE"
            if e["config_snapshot"].get("provider_mode") == "FIXTURE"
            else "REAL_PROVIDER"
        )
        return FindingEvidence(
            finding_id=finding_id,
            mutation_id=m["id"],
            experiment_id=e["id"],
            project_id=e["project_id"],
            question=clean(
                e["config_snapshot"]["target"]["prompt_template"], configured_secrets(s.settings)
            ),
            sampling_options=e["config_snapshot"]["target"]["inference_options"],
            provenance=provenance,
            validation=report,
            validation_history=history,
            assets=links,
            observations=obs,
            usage=totals,
            export_eligible=not reasons,
            export_blocked_reasons=reasons,
            qualification=f"{provenance}: {f['confirmation_failures']}/{f['confirmation_attempts']} fresh incorrect under recorded settings; screening excluded. Provider-managed behavior can change. Demo approval is separate.",
        )
    except (IntegrityError, ValueError, KeyError, TypeError):
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED,
            "Retained finding evidence is incomplete, unsafe, or damaged.",
        ) from None


@dataclass(frozen=True)
class ExportInput:
    manifest: RegressionManifest
    evidence: dict[str, bytes]


def collect_export(s: Service, finding_id: UUID, export_job_id: UUID) -> ExportInput:
    f = s.finding(finding_id)
    ev = finding_evidence(s, finding_id)
    if not ev.export_eligible:
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED,
            "Export requires current sealed VALID evidence, reviewed ground truth, correct baseline, and complete 3/3 fresh incorrect confirmations.",
        )
    e = s.experiment(f["experiment_id"])
    m = s.candidates.row(f["mutation_id"])
    job = s.job(export_job_id)
    if (
        job["kind"] != "EXPORT"
        or job["finding_id"] != finding_id
        or job["experiment_id"] != e["id"]
        or job["project_id"] != e["project_id"]
        or job["request_json"].get("finding_id") != str(finding_id)
    ):
        raise ServiceError(ErrorCode.FORBIDDEN, "Export job references differ from finding.", 403)
    target = e["config_snapshot"]["target"]
    if job["request_json"].get("validation_sha256") != ev.validation.report_sha256:
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED, "Export request validity evidence has changed."
        )
    manifest = RegressionManifest(
        finding_id=finding_id,
        export_job_id=export_job_id,
        parent_mutation_id=m["parent_mutation_id"],
        parent_finding_id=s.db.scalar(
            select(s.t.findings.c.id).where(s.t.findings.c.mutation_id == m["parent_mutation_id"])
        )
        if m["parent_mutation_id"]
        else None,
        minimization_job_id=m["minimization_job_id"],
        test_id=f"finding-{finding_id}",
        question=ev.question,
        expected={
            "kind": "integer",
            "value": e["config_snapshot"]["contract"]["expected"]["value"],
            "verified_by": "human",
        },
        model={
            "adapter": target["adapter_key"],
            "model_id": clean(target["model_id"], configured_secrets(s.settings)),
            "revision": clean(target["model_revision"], configured_secrets(s.settings)),
            "sampling_options": target["inference_options"],
        },
        invariant_verification={
            "verdict": "VALID",
            "reviewed_by": clean(ev.validation.review_audit.actor, configured_secrets(s.settings))
            if ev.validation.review_audit
            else "",
            "verifier_version": ev.validation.verifier_version,
            "report_sha256": ev.validation.report_sha256,
        },
        confirmation={"screening_failures": 1, "fresh_attempts": 3, "fresh_failures": 3},
        mutation={
            "family": m["family"],
            "strength": float(m["strength"]),
            "recipe_version": "1.0.0",
        },
        provenance=ev.provenance,
        recorded_at=job["created_at"],
    )
    evidence = {}
    roles = {
        "original": "original.png",
        "candidate": "failing.png",
        "protected_mask": "protected-mask.png",
        "diff": "comparison.png",
        "recipe": "recipe.json",
        "report": "validity-report.json",
    }
    for role, name in roles.items():
        evidence["evidence/" + name] = s.artifacts.read_verified(ev.assets[role].asset_id)
    evidence["evidence/target-responses.json"] = canonical(
        [o.model_dump(mode="json") for o in ev.observations]
    )
    seed = s.seed(UUID(e["config_snapshot"]["seed"]["id"]))
    prov = seed["provenance_json"]
    source_id = UUID(prov["original_asset_id"])
    source = s.asset(source_id)
    source_bytes = s.artifacts.read_verified(source_id)
    if source["project_id"] != e["project_id"] or source["sha256"] != prov["original_sha256"]:
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Original source binding failed.")
    if source_bytes != evidence["evidence/original.png"]:
        name = "evidence/original-source." + EXTENSIONS[source["media_type"]]
        evidence[name] = source_bytes
        manifest.files["original_source"] = name
    manifest.sha256 = {name: sha256(data).hexdigest() for name, data in evidence.items()}
    return ExportInput(manifest, evidence)


def registered_export_is_current(s: Service, f: Any, frozen: RegressionManifest) -> bool:
    """Cheap list projection of immutable registration plus current DB validity.

    Lists do not re-read large evidence blobs. Detail/download still fully verify
    bytes and accounting; READY here means registered and not revoked by DB state.
    regression_exports.status retains registration state, not effective eligibility.
    """
    from counterforge_verification.invariants import POLICY_VERSION, VERIFIER_VERSION

    latest = s.candidates.latest(f["mutation_id"], required=False)
    if latest is None or not f["confirmation_job_id"]:
        return False
    report = ValidationReport.model_validate(latest["evidence_json"].get("report", {}))
    job = s.job(f["confirmation_job_id"])
    return bool(
        f["status"] == "STABLE"
        and f["confirmation_attempts"] == 3
        and f["confirmation_failures"] == 3
        and latest["verdict"] == "VALID"
        and report.verdict == "VALID"
        and report.review_audit
        and latest["verifier_version"] == VERIFIER_VERSION
        and latest["policy_version"] == POLICY_VERSION
        and report.report_sha256 == frozen.invariant_verification.report_sha256
        and job["state"] == "SUCCEEDED"
        and job["kind"] == "CONFIRM"
        and job["finding_id"] == f["id"]
        and job["experiment_id"] == f["experiment_id"]
    )


def configured_secrets(settings: Any) -> tuple[bytes, ...]:
    return tuple(
        value.encode()
        for value in (
            settings.model_api_key.get_secret_value(),
            settings.owner_auth_token.get_secret_value(),
        )
        if value
    )
