"""Owner-scoped immutable candidate evidence. No target/provider invocation here."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from counterforge_contracts.mutations import (
    ApprovedMask,
    DiffMetrics,
    GeneratedMutation,
    MutationRecipe,
    SeedImage,
)
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.requests import (
    BackgroundRequest,
    CandidateSegmentationRequest,
    CreateMutation,
    MutationReviewRequest,
)
from counterforge_contracts.responses import (
    JobRef,
    Mutation,
    MutationValidationRevision,
    Page,
    ValidationReport,
)
from counterforge_contracts.versions import ErrorCode
from counterforge_contracts.vtc import VisualTestContract
from counterforge_mutations.registry import approve_plan, generate_mutation
from counterforge_verification.invariants import (
    POLICY_VERSION,
    VERIFIER_VERSION,
    VerificationPolicy,
    aggregate_verdict,
    report_digest,
    seal_report,
    verify_candidate,
)
from counterforge_verification.review import GroundTruthAudit, HumanReview, resolve_review
from sqlalchemy import func, select

from .accounting import Budget
from .artifacts import IntegrityError
from .db.repositories import IdempotencyConflict
from .errors import ServiceError
from .redaction import review_text

if TYPE_CHECKING:
    from .service import Service


class Candidates:
    def __init__(self, service: Service) -> None:
        self.s = service

    def clean(self, value: str) -> str:
        return review_text(
            value,
            self.s.settings.owner_auth_token.get_secret_value(),
            self.s.settings.model_api_key.get_secret_value(),
        )

    def clean_plan(self, plan: MutationPlan) -> MutationPlan:
        return type(plan).model_validate(
            {
                **plan.model_dump(mode="json"),
                "prompt": self.clean(plan.prompt),
                "hypothesis": self.clean(plan.hypothesis),
            }
        )

    def snapshot(
        self, contract_id: UUID, snapshot: dict[str, Any], project_id: UUID
    ) -> tuple[VisualTestContract, SeedImage, tuple[ApprovedMask, ...]]:
        """Read frozen approvals, never compare them to the mutable current draft."""
        s = self.s
        try:
            contract = s.row(s.t.visual_contracts, contract_id)
            seed = s.seed(UUID(snapshot["seed"]["id"]))
            vtc = VisualTestContract.model_validate(snapshot["contract"])
            from .service import fingerprint

            if seed["project_id"] != project_id or contract["seed_id"] != seed["id"]:
                raise ServiceError(ErrorCode.FORBIDDEN, "Evidence is outside this project.", 403)
            if (
                fingerprint(vtc.model_dump(mode="json")) != snapshot["contract_sha256"]
                or snapshot["contract_sha256"] != contract["contract_sha256"]
                or snapshot["contract"] != contract["contract_json"]
            ):
                raise IntegrityError("Contract snapshot binding failed")
            frozen_seed = snapshot["seed"]
            source_id = UUID(frozen_seed["image_asset_id"])
            provenance = frozen_seed["provenance"]
            if (
                source_id != UUID(provenance["normalized_asset_id"])
                or frozen_seed["image_sha256"] != provenance["normalized_sha256"]
                or source_id != seed["image_asset_id"]
            ):
                raise IntegrityError("Source snapshot binding failed")
            for aid, sha, kind in [
                (source_id, frozen_seed["image_sha256"], "SEED_IMAGE"),
                (
                    UUID(provenance["original_asset_id"]),
                    provenance["original_sha256"],
                    "SEED_IMAGE",
                ),
            ]:
                self.read_asset(project_id, aid, sha, kind)
            source = SeedImage(
                source_id,
                seed["id"],
                s.artifacts.read_verified(source_id),
                frozen_seed["image_sha256"],
            )
            frozen_masks = snapshot["masks"]
            if [m["asset_id"] for m in frozen_masks] != [
                str(i) for i in vtc.protected.mask_asset_ids
            ] or len(frozen_masks) != vtc.expected.value:
                raise IntegrityError("Mask references failed")
            masks = []
            ordinals = []
            for item in frozen_masks:
                if (
                    not item["reviewer"]
                    or not item["reviewed_at"]
                    or item["revision"] < 1
                    or item["object_label"] != vtc.protected.label
                ):
                    raise IntegrityError("Mask approval snapshot failed")
                aid = UUID(item["asset_id"])
                # The immutable approved snapshot is authority after lock. Current
                # mask rows may have advanced to new revisions/assets or retired.
                data = self.read_asset(project_id, aid, item["sha256"], "MASK")
                masks.append(
                    ApprovedMask(
                        aid, seed["id"], source.sha256, data, item["sha256"], item["reviewer"]
                    )
                )
                ordinals.append(item["object_ordinal"])
            if len(set(ordinals)) != len(ordinals):
                raise IntegrityError("Duplicate instance identity")
            return vtc, source, tuple(masks)
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, IntegrityError):
                raise
            raise IntegrityError("Frozen evidence is incomplete") from None

    def canonical_inputs(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Immutable content identity of one validated frozen snapshot.

        Reviewer names, approval timestamps and draft revisions are approval
        provenance, not generation inputs: re-approving the exact same reviewed
        mask bytes must not make an already paid background unusable. Callers
        validate each snapshot separately and compare only this identity.
        """
        seed = snapshot["seed"]
        return {
            "contract_sha256": snapshot["contract_sha256"],
            "seed": {
                "id": seed["id"],
                "image_asset_id": seed["image_asset_id"],
                "image_sha256": seed["image_sha256"],
                "provenance": seed["provenance"],
            },
            "masks": [
                {
                    "asset_id": mask["asset_id"],
                    "sha256": mask["sha256"],
                    "object_label": mask["object_label"],
                    "object_ordinal": mask["object_ordinal"],
                }
                for mask in snapshot["masks"]
            ],
        }

    def aliased(self, experiment_id: UUID, key: str, identity: str) -> Any | None:
        """Resolve one requested intent to its canonical candidate, if bound."""
        t = self.s.t.mutation_request_aliases
        row = (
            self.s.db.execute(
                select(t).where(t.c.experiment_id == experiment_id, t.c.idempotency_key == key)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["request_sha256"] != identity:
            raise IdempotencyConflict("Candidate request differs")
        return self.s.row(self.s.t.mutations, row["mutation_id"])

    def alias(
        self,
        experiment_id: UUID,
        key: str,
        identity: str,
        mutation_id: UUID,
        request: dict[str, Any],
    ) -> None:
        """Append-only binding of one requested plan/seed/background intent."""
        self.s.db.execute(
            self.s.t.mutation_request_aliases.insert().values(
                experiment_id=experiment_id,
                mutation_id=mutation_id,
                idempotency_key=key,
                request_sha256=identity,
                request_json=request,
            )
        )

    def read_asset(self, project_id: UUID, aid: UUID, sha: str, kind: str | None = None) -> bytes:
        a = self.s.asset(aid)
        if a["project_id"] != project_id:
            raise ServiceError(ErrorCode.FORBIDDEN, "Artifact is outside this project.", 403)
        if a["sha256"] != sha or (kind and a["kind"] != kind):
            raise IntegrityError("Artifact binding failed")
        return self.s.artifacts.read_verified(aid)

    def row(self, mutation_id: UUID, *, lock: bool = False) -> Any:
        if lock:
            probe = self.s.row(self.s.t.mutations, mutation_id)
            self.s.row(self.s.t.experiments, probe["experiment_id"], lock=True)
        row = self.s.row(self.s.t.mutations, mutation_id, lock=lock)
        self.s.experiment(row["experiment_id"])
        return row

    def latest(self, mutation_id: UUID, *, required: bool = True) -> Any:
        t = self.s.t.mutation_validations
        row = (
            self.s.db.execute(
                select(t)
                .where(t.c.mutation_id == mutation_id)
                .order_by(t.c.revision.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if row is None and required:
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Candidate has no verifier evidence.")
        return row

    def append(
        self,
        row: Any,
        report: ValidationReport,
        *,
        ground_truth_audit: dict[str, Any] | None = None,
        sam: dict[str, Any] | None = None,
    ) -> Any:
        """Caller holds candidate lock. IDs exist before report sealing (no self ref)."""
        from .service import canonical_json

        s = self.s
        project = s.experiment(row["experiment_id"])["project_id"]
        previous = (
            s.db.execute(
                select(s.t.mutation_validations)
                .where(s.t.mutation_validations.c.mutation_id == row["id"])
                .order_by(s.t.mutation_validations.c.revision.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        audit_asset = None
        if ground_truth_audit is not None:
            audit_asset = s.artifacts.put(
                project, canonical_json(ground_truth_audit), "OTHER", "application/json"
            )
            report.evidence_asset_ids = [*report.evidence_asset_ids, audit_asset.id]
        report = seal_report(report)
        asset = s.artifacts.put(
            project, canonical_json(report.model_dump(mode="json")), "OTHER", "application/json"
        )
        record = (
            s.db.execute(
                s.t.mutation_validations.insert()
                .values(
                    mutation_id=row["id"],
                    verifier_version=report.verifier_version,
                    policy_version=report.policy_version,
                    verdict=report.verdict,
                    evidence_json={
                        "report": report.model_dump(mode="json"),
                        "report_asset_id": str(asset.id),
                        "report_asset_sha256": asset.sha256,
                        "ground_truth_audit": ground_truth_audit,
                        "ground_truth_audit_asset_id": str(audit_asset.id) if audit_asset else None,
                        "sam": sam
                        if sam is not None
                        else (previous["evidence_json"].get("sam") if previous else None),
                    },
                    reviewer=s.settings.owner_label if report.review_audit else None,
                    reviewed_at=func.now() if report.review_audit else None,
                )
                .returning(s.t.mutation_validations)
            )
            .mappings()
            .one()
        )
        s.db.execute(
            s.t.mutations.update()
            .where(s.t.mutations.c.id == row["id"])
            .values(state=report.verdict, updated_at=func.now())
        )
        s.audit(
            project,
            "MUTATION_VALIDATED",
            "mutation",
            row["id"],
            validation_id=str(record["id"]),
            revision=record["revision"],
            report_sha256=report.report_sha256,
            previous_validation_id=str(record["previous_validation_id"])
            if record["previous_validation_id"]
            else None,
            verdict=str(report.verdict),
            review=report.review_audit.model_dump(mode="json") if report.review_audit else None,
        )
        from .experiments import event

        event(
            s,
            row["experiment_id"],
            "CANDIDATE_VALIDATED",
            {
                "mutation_id": str(row["id"]),
                "verdict": str(report.verdict),
                "revision": record["revision"],
                "report_sha256": report.report_sha256,
            },
        )
        return record

    def create(
        self,
        experiment_id: UUID,
        body: CreateMutation,
        key: str,
        *,
        source_plan_id: UUID | None = None,
        automated: bool = False,
        refinement_job_id: UUID | None = None,
    ) -> Mutation:
        from .service import canonical_json, fingerprint

        s = self.s
        experiment = s.row(s.t.experiments, experiment_id, lock=True)
        s.project(experiment["project_id"])
        vtc, source, masks = self.snapshot(
            experiment["contract_id"], experiment["config_snapshot"], experiment["project_id"]
        )
        try:
            approved = approve_plan(vtc, self.clean_plan(body.plan))
        except ValueError:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Mutation plan is not approved by the contract.", 422
            ) from None
        if source_plan_id is not None:
            origin = s.row(s.t.mutation_plans, source_plan_id)
            if origin["experiment_id"] != experiment_id:
                raise ServiceError(
                    ErrorCode.FORBIDDEN, "Planner proposal belongs to another experiment.", 403
                )
            origin_plan = approve_plan(
                vtc, self.clean_plan(MutationPlan.model_validate(origin["plan_json"]))
            )
            if origin_plan.plan_json != approved.plan_json:
                raise ServiceError(
                    ErrorCode.INVALID_INPUT,
                    "Candidate differs from retained planner proposal.",
                    422,
                )
        request = {
            "plan": approved.plan_json,
            "rng_seed": body.rng_seed,
            "background_job_id": str(body.background_job_id) if body.background_job_id else None,
        }
        if source_plan_id is not None:
            request["source_plan_id"] = str(source_plan_id)
        parent_id = None
        if refinement_job_id is not None:
            context = Budget(s.uow).require_followup(experiment, refinement_job_id)
            parent_id = UUID(context["request_json"]["source_mutation_id"])
            parent = self.row(parent_id)
            if parent["experiment_id"] != experiment_id:
                raise ServiceError(ErrorCode.FORBIDDEN, "Source lineage differs.", 403)
            request["refinement_job_id"] = str(refinement_job_id)
            request["parent_mutation_id"] = str(parent_id)
        identity = fingerprint(request)
        aliased = self.aliased(experiment_id, key, identity)
        if aliased is not None:
            return self.dto(aliased)
        previous = (
            s.db.execute(
                select(s.t.mutations).where(
                    s.t.mutations.c.experiment_id == experiment_id,
                    s.t.mutations.c.idempotency_key == key,
                )
            )
            .mappings()
            .one_or_none()
        )
        if previous is not None:
            if previous["request_sha256"] != identity:
                raise IdempotencyConflict("Candidate request differs")
            return self.dto(previous)
        budget = Budget(s.uow)
        if refinement_job_id is None:
            budget._require_runnable(experiment)
        else:
            budget.require_followup(experiment, refinement_job_id)
        background = None
        background_snapshot = None
        synthetic = False
        if body.background_job_id:
            job = s.job(body.background_job_id)
            r = job["request_json"]
            job_snapshot = r.get("snapshot")
            if (
                job["kind"] != "SCENE"
                or job["state"] != "SUCCEEDED"
                or job["project_id"] != experiment["project_id"]
                or r.get("contract_id") != str(experiment["contract_id"])
                or r.get("plan_json") != approved.plan_json
                or not isinstance(job_snapshot, dict)
                or r.get("source_sha256") != source.sha256
            ):
                raise ServiceError(
                    ErrorCode.INVALID_INPUT,
                    "Background job does not match frozen candidate inputs.",
                    422,
                )
            # Validate both approved snapshots independently, then require only
            # their immutable content identity to agree: an unchanged mask that
            # was reviewed again must not discard an already paid background.
            self.snapshot(experiment["contract_id"], job_snapshot, experiment["project_id"])
            if self.canonical_inputs(job_snapshot) != self.canonical_inputs(
                experiment["config_snapshot"]
            ):
                raise ServiceError(
                    ErrorCode.INVALID_INPUT,
                    "Background job does not match frozen candidate inputs.",
                    422,
                )
            result = job["result_json"]
            background = self.read_asset(
                experiment["project_id"],
                UUID(result["background_asset_id"]),
                result["background_sha256"],
                "OTHER",
            )
            synthetic = bool(result["synthetic"])
            # Retain which approved snapshot the paid background was made against.
            background_snapshot = r.get("snapshot_sha256")
        if body.plan.requires_muse_image != (background is not None):
            raise ServiceError(
                ErrorCode.INVALID_INPUT,
                "Generative plan requires a matching completed background job.",
                422,
            )
        try:
            generated = generate_mutation(
                source, masks, approved, body.rng_seed, background_bytes=background
            )
        except ValueError:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Mutation inputs could not be accepted.", 422
            ) from None
        # Bound CPU work by the same experiment runtime, including generation.
        if refinement_job_id is None:
            budget._require_runnable(experiment)
        else:
            budget.require_followup(experiment, refinement_job_id)
        project = experiment["project_id"]
        # Content addressed storage makes this asset the normalized candidate SHA.
        candidate = s.artifacts.put(
            project,
            generated.png,
            "MUTATION_IMAGE",
            "image/png",
            width=generated.recipe.width,
            height=generated.recipe.height,
            metadata={"synthetic": synthetic, "implementation": "phase04-1"},
        )
        canonical = (
            s.db.execute(
                select(s.t.mutations).where(
                    s.t.mutations.c.experiment_id == experiment_id,
                    s.t.mutations.c.image_asset_id == candidate.id,
                    s.t.mutations.c.recipe_sha256.is_not(None),
                )
            )
            .mappings()
            .one_or_none()
        )
        if canonical is not None:
            # Identical pixels are one reviewable identity: a different key, seed
            # or plan cannot mint fresh human gates, cannot discard the recorded
            # verdict history and does not consume a candidate slot. The original
            # recipe and latest report of the canonical row stay authoritative.
            self.alias(experiment_id, key, identity, canonical["id"], request)
            s.audit(
                project,
                "CANDIDATE_REQUEST_ALIASED",
                "mutation",
                canonical["id"],
                request_sha256=identity,
                candidate_sha256=candidate.sha256,
            )
            return self.dto(canonical)
        _, pending = budget._pending(experiment_id)
        if experiment["candidates_created"] + pending >= experiment["max_candidates"]:
            raise ServiceError(ErrorCode.BUDGET_EXCEEDED, "Candidate limit was reached.")
        assets = {
            "original": str(source.asset_id),
            "original_upload": experiment["config_snapshot"]["seed"]["provenance"][
                "original_asset_id"
            ],
            "candidate": str(candidate.id),
        }
        hashes = {
            "original": source.sha256,
            "original_upload": experiment["config_snapshot"]["seed"]["provenance"][
                "original_sha256"
            ],
            "candidate": candidate.sha256,
        }
        for role, data, kind, media in [
            ("background", generated.background_png, "OTHER", "image/png"),
            ("protected_union", generated.protected_union_png, "MASK", "image/png"),
            ("diff", generated.diff_png, "DIFF", "image/png"),
            (
                "recipe",
                canonical_json(generated.recipe.model_dump(mode="json")),
                "OTHER",
                "application/json",
            ),
        ]:
            a = s.artifacts.put(
                project,
                data,
                kind,
                media,
                width=generated.recipe.width if media == "image/png" else None,
                height=generated.recipe.height if media == "image/png" else None,
                metadata={"synthetic": synthetic, "implementation": "phase04-1"},
            )
            assets[role] = str(a.id)
            hashes[role] = a.sha256
        if body.background_job_id:
            result = s.job(body.background_job_id)["result_json"]
            assets["provider_background"] = result["original_background_asset_id"]
            hashes["provider_background"] = result["original_background_sha256"]
        plan_id = source_plan_id
        if plan_id is None:
            plan_id = s.db.scalar(
                s.t.mutation_plans.insert()
                .values(
                    experiment_id=experiment_id,
                    planner_model="EXPERIMENT_SCHEDULER" if automated else "OWNER_APPROVED",
                    strategy="RANDOM"
                    if experiment["mode"] == "RANDOM_BASELINE"
                    else "META_ADAPTIVE",
                    hypothesis=self.clean(body.plan.hypothesis),
                    plan_json=__import__("json").loads(approved.plan_json),
                )
                .returning(s.t.mutation_plans.c.id)
            )
        row = (
            s.db.execute(
                s.t.mutations.insert()
                .values(
                    experiment_id=experiment_id,
                    plan_id=plan_id,
                    parent_mutation_id=parent_id,
                    minimization_job_id=refinement_job_id,
                    image_asset_id=UUID(assets["candidate"]),
                    family=generated.recipe.family,
                    strength=Decimal(str(generated.recipe.strength)),
                    generator="COMPOSITE" if background else "DETERMINISTIC",
                    recipe_json=generated.recipe.model_dump(mode="json"),
                    recipe_sha256=hashes["recipe"],
                    state="VALIDATING",
                    idempotency_key=key,
                    request_sha256=identity,
                    evidence_json={
                        "assets": assets,
                        "asset_hashes": hashes,
                        "metrics": asdict(generated.metrics),
                        "mask_snapshot": experiment["config_snapshot"]["masks"],
                        "synthetic": synthetic,
                        "background_job_id": str(body.background_job_id)
                        if body.background_job_id
                        else None,
                        "background_snapshot_sha256": background_snapshot,
                    },
                )
                .returning(s.t.mutations)
            )
            .mappings()
            .one()
        )
        self.alias(experiment_id, key, identity, row["id"], request)
        s.db.execute(
            s.t.experiments.update()
            .where(s.t.experiments.c.id == experiment_id)
            .values(candidates_created=s.t.experiments.c.candidates_created + 1)
        )
        report = verify_candidate(
            vtc,
            source,
            generated,
            VerificationPolicy(
                evidence_asset_ids=tuple(
                    dict.fromkeys(
                        [*(UUID(x) for x in assets.values()), *(m.asset_id for m in masks)]
                    )
                )
            ),
        )
        self.append(row, report)
        return self.dto(self.row(row["id"]))

    def retained(
        self, row: Any
    ) -> tuple[VisualTestContract, SeedImage, GeneratedMutation, VerificationPolicy]:
        from .service import fingerprint

        s = self.s
        e = s.experiment(row["experiment_id"])
        vtc, source, masks = self.snapshot(e["contract_id"], e["config_snapshot"], e["project_id"])
        ev = row["evidence_json"]
        assets = ev["assets"]
        hashes = ev["asset_hashes"]
        data = {k: self.read_asset(e["project_id"], UUID(a), hashes[k]) for k, a in assets.items()}
        recipe = MutationRecipe.model_validate(row["recipe_json"])
        if (
            fingerprint(row["recipe_json"]) != row["recipe_sha256"]
            or data["recipe"]
            != __import__("json")
            .dumps(
                row["recipe_json"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            .encode()
            or hashes["recipe"] != row["recipe_sha256"]
            or assets["original"] != str(source.asset_id)
            or assets["candidate"] != str(row["image_asset_id"])
            or row["family"] != recipe.family
            or float(row["strength"]) != recipe.strength
            or ev["mask_snapshot"] != e["config_snapshot"]["masks"]
        ):
            raise IntegrityError("Recipe/evidence binding failed")
        generated = GeneratedMutation(
            data["candidate"],
            recipe,
            data["protected_union"],
            data["diff"],
            data["background"],
            masks,
            DiffMetrics(**ev["metrics"]),
        )
        latest = self.latest(row["id"])
        sam = latest["evidence_json"].get("sam")
        ids = list(
            dict.fromkeys([*(UUID(x) for x in assets.values()), *(m.asset_id for m in masks)])
        )
        kwargs: dict[str, Any] = {}
        if sam:
            self.read_asset(e["project_id"], UUID(sam["asset_id"]), sam["sha256"])
            for item in sam["masks"]:
                self.read_asset(e["project_id"], UUID(item["asset_id"]), item["sha256"], "MASK")
            kwargs = {
                "sam_outcome": sam["outcome"],
                "sam_count": sam["count"],
                "sam_evidence_sha256": sam["sha256"],
            }
            ids.extend(UUID(item["asset_id"]) for item in sam["masks"])
            ids.append(UUID(sam["asset_id"]))
        return vtc, source, generated, VerificationPolicy(evidence_asset_ids=tuple(ids), **kwargs)

    def checked_report(self, row: Any) -> ValidationReport:
        from .service import canonical_json

        latest = self.latest(row["id"])
        ev = latest["evidence_json"]
        if "report" not in ev or not ev.get("report_asset_id"):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Legacy validation lacks sealed candidate evidence."
            )
        # Pin the exact verifier/policy this deployment can still recompute. An
        # older sealed report is unresolved evidence, not a recomputation fault:
        # fail closed to REVIEW_REQUIRED instead of an incidental server error.
        if (
            latest["verifier_version"] != VERIFIER_VERSION
            or latest["policy_version"] != POLICY_VERSION
            or ev["report"].get("verifier_version") != VERIFIER_VERSION
            or ev["report"].get("policy_version") != POLICY_VERSION
        ):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "Sealed candidate evidence uses an unsupported verifier policy version.",
            )
        report = ValidationReport.model_validate(ev["report"])
        project = self.s.experiment(row["experiment_id"])["project_id"]
        body = self.read_asset(project, UUID(ev["report_asset_id"]), ev["report_asset_sha256"])
        if (
            body != canonical_json(report.model_dump(mode="json"))
            or report.report_sha256 != report_digest(report)
            or latest["verdict"] != report.verdict
            # A stored verdict never outranks its own mandatory gates.
            or aggregate_verdict(report.checks) != report.verdict
        ):
            raise IntegrityError("Report binding failed")
        if ev.get("ground_truth_audit") is not None:
            audit = ev["ground_truth_audit"]
            aid = UUID(ev["ground_truth_audit_asset_id"])
            from .service import fingerprint

            if (
                not report.review_audit
                or report.review_audit.ground_truth_audit_sha256 != fingerprint(audit)
            ):
                raise IntegrityError("Audit binding failed")
            if self.read_asset(project, aid, fingerprint(audit)) != canonical_json(audit):
                raise IntegrityError("Audit evidence failed")
        vtc, source, generated, policy = self.retained(row)
        recomputed = verify_candidate(vtc, source, generated, policy)
        if recomputed.evidence_hashes != report.evidence_hashes or [
            c for c in recomputed.checks if not c.code.startswith("HUMAN_")
        ] != [c for c in report.checks if not c.code.startswith("HUMAN_")]:
            raise IntegrityError("Current verifier binding failed")
        for aid in report.evidence_asset_ids:
            a = self.s.asset(aid)
            if a["project_id"] != project:
                raise IntegrityError("Report asset ownership failed")
            self.s.artifacts.read_verified(aid)
        return report

    def review(self, mutation_id: UUID, body: MutationReviewRequest) -> Mutation:
        s = self.s
        row = self.row(mutation_id, lock=True)
        report = self.checked_report(row)
        if (
            report.report_sha256 != body.prior_report_sha256
            or report.evidence_hashes != body.evidence_hashes
        ):
            raise ServiceError(ErrorCode.IDEMPOTENCY_CONFLICT, "Review evidence is stale.", 409)
        audit = None
        if body.ground_truth_audit:
            audit = GroundTruthAudit(
                s.settings.owner_label,
                self.clean(body.ground_truth_audit.note),
                body.prior_report_sha256,
                body.evidence_hashes,
                tuple(body.ground_truth_audit.resolved_check_codes),
            )
        try:
            reviewed = resolve_review(
                report,
                HumanReview(
                    s.settings.owner_label,
                    self.clean(body.notes),
                    body.prior_report_sha256,
                    body.evidence_hashes,
                    body.semantic,
                    body.visibility,
                    audit,
                ),
            )
        except ValueError:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Review cannot resolve these verifier gates.", 422
            ) from None
        self.append(row, reviewed, ground_truth_audit=asdict(audit) if audit else None)
        return self.dto(self.row(mutation_id))

    def require_valid_candidate(self, mutation_id: UUID) -> Mutation:
        row = self.row(mutation_id, lock=True)
        report = self.checked_report(row)
        if report.verdict != "VALID":
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Current sealed candidate evidence must be VALID."
            )
        return self.dto(row)

    def dto(self, row: Any) -> Mutation:
        ev = row["evidence_json"]
        latest = self.latest(row["id"], required=False)
        latest_evidence = latest["evidence_json"] if latest else {}
        # Legacy private evidence is never returned as arbitrary JSON.
        report = (
            ValidationReport.model_validate(latest_evidence["report"])
            if "report" in latest_evidence
            else None
        )
        snapshot = self.s.experiment(row["experiment_id"])["config_snapshot"]
        assets = dict(ev.get("assets", {}))
        hashes = dict(ev.get("asset_hashes", {}))
        if report:
            assets["report"] = latest["evidence_json"]["report_asset_id"]
            hashes["report"] = latest["evidence_json"]["report_asset_sha256"]
            if latest["evidence_json"].get("ground_truth_audit_asset_id") and report.review_audit:
                assets["ground_truth_audit"] = latest["evidence_json"][
                    "ground_truth_audit_asset_id"
                ]
                hashes["ground_truth_audit"] = report.review_audit.ground_truth_audit_sha256
        sam = latest_evidence.get("sam")
        if sam:
            assets["sam"] = sam["asset_id"]
            hashes["sam"] = sam["sha256"]
            for index, mask in enumerate(sam["masks"]):
                assets[f"sam_mask:{index}"] = mask["asset_id"]
                hashes[f"sam_mask:{index}"] = mask["sha256"]
        return Mutation(
            id=row["id"],
            experiment_id=row["experiment_id"],
            parent_mutation_id=row["parent_mutation_id"],
            minimization_job_id=row["minimization_job_id"],
            image_asset_id=row["image_asset_id"],
            family=row["family"],
            strength=float(row["strength"]),
            state=row["state"],
            validation=report,
            validation_revision=latest["revision"] if latest else None,
            recipe=row["recipe_json"] if row["recipe_sha256"] else None,
            assets=assets,
            asset_hashes=hashes,
            human_expected_count=snapshot.get("contract", {}).get("expected", {}).get("value"),
            protected_label=snapshot.get("contract", {}).get("protected", {}).get("label"),
            source_seed_id=snapshot.get("seed", {}).get("id"),
            mask_snapshot=ev.get("mask_snapshot", []),
            synthetic=ev.get("synthetic", False),
        )

    def list(
        self, experiment_id: UUID, limit: int, cursor: str | None, status: str | None
    ) -> Page[Mutation]:
        s = self.s
        s.experiment(experiment_id)
        t = s.t.mutations
        conditions = [t.c.experiment_id == experiment_id]
        if status:
            conditions.append(t.c.state == status)
        return s.page(t, f"mutations:{experiment_id}:{status}", self.dto, conditions, limit, cursor)

    def history(
        self, mutation_id: UUID, limit: int, cursor: str | None
    ) -> Page[MutationValidationRevision]:
        self.row(mutation_id)
        s = self.s
        t = s.t.mutation_validations

        def render(row: Any) -> MutationValidationRevision:
            ev = row["evidence_json"]
            return MutationValidationRevision(
                id=row["id"],
                mutation_id=mutation_id,
                revision=row["revision"],
                previous_validation_id=row["previous_validation_id"],
                report=ValidationReport.model_validate(ev["report"]) if "report" in ev else None,
                report_asset_id=UUID(ev["report_asset_id"]) if ev.get("report_asset_id") else None,
                ground_truth_audit_asset_id=UUID(ev["ground_truth_audit_asset_id"])
                if ev.get("ground_truth_audit_asset_id")
                else None,
                ground_truth_audit=ev.get("ground_truth_audit"),
                created_at=row["created_at"],
            )

        # Scope-bound cursor encodes row identity; ordinal pagination is deterministic.
        scope = f"mutation-history:{mutation_id}"
        q = select(t).where(t.c.mutation_id == mutation_id)
        if cursor:
            previous = s.row(t, s._cursor_id(scope, cursor))
            if previous["mutation_id"] != mutation_id:
                raise ServiceError(ErrorCode.INVALID_INPUT, "Invalid history cursor.", 422)
            q = q.where(t.c.revision > previous["revision"])
        rows = s.db.execute(q.order_by(t.c.revision).limit(limit + 1)).mappings().all()
        return Page(
            items=[render(r) for r in rows[:limit]],
            next_cursor=s._cursor(scope, rows[limit - 1]["id"]) if len(rows) > limit else None,
        )

    def request_background(self, contract_id: UUID, body: BackgroundRequest, key: str) -> JobRef:
        from .service import fingerprint

        s = self.s
        contract = s.contract(contract_id)
        seed = s.seed(contract["seed_id"])
        vtc = VisualTestContract.model_validate(contract["contract_json"])
        try:
            approved = approve_plan(vtc, self.clean_plan(body.plan))
            if (
                not body.plan.requires_muse_image
                or body.plan.target_region != "background_only"
                or body.plan.family == "brightness_shift"
            ):
                raise ValueError
        except ValueError:
            raise ServiceError(
                ErrorCode.INVALID_INPUT,
                "Scene job requires an allowed generative background plan.",
                422,
            ) from None
        snapshot = {
            "contract": contract["contract_json"],
            "contract_sha256": contract["contract_sha256"],
            **contract["evidence_snapshot_json"],
        }
        self.snapshot(contract_id, snapshot, seed["project_id"])
        request = {
            "contract_id": str(contract_id),
            "seed_id": str(seed["id"]),
            "snapshot": snapshot,
            "snapshot_sha256": fingerprint(snapshot),
            "source_sha256": snapshot["seed"]["image_sha256"],
            "plan_json": approved.plan_json,
            "limits": body.limits.model_dump(mode="json"),
        }
        job = s.uow.background_jobs.enqueue(
            project_id=seed["project_id"], kind="SCENE", request_json=request, idempotency_key=key
        )
        s.audit(
            seed["project_id"],
            "BACKGROUND_REQUESTED",
            "job",
            job,
            contract_id=str(contract_id),
            preparatory_usage=True,
        )
        return s.job_dto(s.job(job))

    def request_segmentation(
        self, mutation_id: UUID, body: CandidateSegmentationRequest, key: str
    ) -> JobRef:
        s = self.s
        row = self.row(mutation_id, lock=True)
        self.checked_report(row)
        e = s.experiment(row["experiment_id"])
        request = {
            "mutation_id": str(mutation_id),
            "source_sha256": row["evidence_json"]["asset_hashes"]["candidate"],
            "limits": body.limits.model_dump(mode="json"),
        }
        job = s.uow.background_jobs.enqueue(
            project_id=e["project_id"],
            kind="MUTATION_SAM",
            request_json=request,
            idempotency_key=key,
        )
        return s.job_dto(s.job(job))
