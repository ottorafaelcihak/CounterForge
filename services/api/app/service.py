"""Owner-scoped application services; repositories never grant authorization."""

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from counterforge_contracts.requests import (
    CreateProject,
    CreateTargetConfig,
    ReviewDecision,
    SegmentationRequest,
    StartExperiment,
    TargetCheckRequest,
)
from counterforge_contracts.responses import (
    CheckEvidence,
    Contract,
    ExperimentLinks,
    ExperimentRef,
    ExperimentView,
    ExportLinks,
    ExportRef,
    Finding,
    FindingEvidence,
    JobLinks,
    JobRef,
    JobResult,
    JobUsage,
    Mask,
    MaskGeometry,
    Metrics,
    Page,
    Project,
    Seed,
    SeedProvenance,
    Target,
    ValidationReport,
)
from counterforge_contracts.versions import ErrorCode
from counterforge_contracts.vtc import ExpectedCount, Limits, VisualTestContract
from sqlalchemy import Table, case, func, literal, select, union_all
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.elements import ColumnElement

from .artifacts import ArtifactStore, normalize_image
from .candidates import Candidates
from .config import Settings
from .db.repositories import IdempotencyConflict
from .db.uow import UnitOfWork
from .errors import ServiceError
from .mask_review import MaskReview
from .redaction import review_text


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class Service:
    def __init__(self, uow: UnitOfWork, settings: Settings) -> None:
        self.uow = uow
        self.db = uow.connection
        self.t = uow.tables
        self.settings = settings
        self.artifacts = ArtifactStore(uow, settings.artifact_root)
        self.mask_review = MaskReview(self)
        self.candidates = Candidates(self)

    def row(self, table: Table, entity_id: UUID, *, lock: bool = False) -> RowMapping:
        query = select(table).where(table.c.id == entity_id)
        if lock:
            query = query.with_for_update()
        row = self.db.execute(query).mappings().one_or_none()
        if row is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Entity was not found.", 404)
        return row

    def project(self, project_id: UUID, *, lock: bool = False) -> RowMapping:
        row = self.row(self.t.projects, project_id, lock=lock)
        if row["owner_label"] != self.settings.owner_label:
            raise ServiceError(ErrorCode.FORBIDDEN, "Project is not owned by this owner.", 403)
        return row

    def seed(self, seed_id: UUID, *, lock: bool = False) -> RowMapping:
        row = self.row(self.t.seed_cases, seed_id, lock=lock)
        self.project(row["project_id"])
        return row

    def asset(self, asset_id: UUID) -> RowMapping:
        row = self.row(self.t.artifact_assets, asset_id)
        self.project(row["project_id"])
        return row

    def audit(
        self, project_id: UUID, action: str, entity_type: str, entity_id: UUID, **details: Any
    ) -> None:
        self.db.execute(
            self.t.audit_events.insert().values(
                project_id=project_id,
                actor=self.settings.owner_label,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id),
                details_json=details,
            )
        )

    @staticmethod
    def project_dto(row: RowMapping) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=row["created_at"],
        )

    @staticmethod
    def seed_dto(row: RowMapping) -> Seed:
        provenance = SeedProvenance.model_validate(row["provenance_json"])
        return Seed(
            id=row["id"],
            project_id=row["project_id"],
            image_asset_id=row["image_asset_id"],
            sha256=provenance.original_sha256,
            question=row["question"],
            expected=ExpectedCount.model_validate(row["expected_json"]),
            label_provenance=row["label_provenance"],
            case_key=row["case_key"],
            provenance=provenance,
        )

    @staticmethod
    def mask_dto(row: Mapping[str, Any] | RowMapping) -> Mask:
        raw_geometry = row["geometry_json"]
        geometry = (
            MaskGeometry.model_validate(
                {
                    key: raw_geometry[key]
                    for key in ("width", "height", "bbox_xyxy", "foreground_pixels", "binary")
                }
            )
            if all(
                key in raw_geometry
                for key in ("width", "height", "bbox_xyxy", "foreground_pixels", "binary")
            )
            else None
        )
        return Mask(
            revision=row.get("revision", 1),
            active=row.get("active", True),
            origin=row.get("origin", "SAM"),
            geometry=geometry,
            **{
                name: row[name]
                for name in (
                    "id",
                    "seed_id",
                    "mask_asset_id",
                    "object_label",
                    "object_ordinal",
                    "reviewed",
                    "reviewer",
                    "reviewed_at",
                )
            },
        )

    def create_project(self, body: CreateProject) -> Project:
        row = (
            self.db.execute(
                self.t.projects.insert()
                .values(**body.model_dump(), owner_label=self.settings.owner_label)
                .returning(self.t.projects)
            )
            .mappings()
            .one()
        )
        self.audit(row["id"], "PROJECT_CREATED", "project", row["id"])
        return self.project_dto(row)

    def create_seed(
        self,
        project_id: UUID,
        data: bytes,
        media_type: str,
        *,
        question: str,
        expected_count: int,
        license_ack: bool,
        label_reviewer: str,
    ) -> Seed:
        self.project(project_id)
        question = question.strip()
        if not 10 <= len(question) <= 300:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Question must contain 10–300 characters.", 422
            )
        reviewer = label_reviewer.strip()
        if not license_ack or not reviewer:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Rights and human label review are required.", 422
            )
        image = normalize_image(data, media_type)
        original = self.artifacts.put(
            project_id,
            data,
            "SEED_IMAGE",
            media_type,
            width=image.original_width,
            height=image.original_height,
        )
        normalized = self.artifacts.put(
            project_id,
            image.png,
            "SEED_IMAGE",
            "image/png",
            width=image.width,
            height=image.height,
            metadata={"normalization_policy": image.policy, "pixel_sha256": image.pixel_sha256},
        )
        provenance = SeedProvenance(
            original_asset_id=original.id,
            normalized_asset_id=normalized.id,
            original_sha256=original.sha256,
            normalized_sha256=normalized.sha256,
            pixel_sha256=image.pixel_sha256,
            normalization_policy=image.policy,
        )
        seed_id = uuid4()
        row = (
            self.db.execute(
                self.t.seed_cases.insert()
                .values(
                    id=seed_id,
                    project_id=project_id,
                    image_asset_id=normalized.id,
                    case_key=f"seed-{seed_id}",
                    question=question,
                    expected_json=ExpectedCount(value=expected_count, reviewer=reviewer).model_dump(
                        mode="json"
                    ),
                    label_provenance="HUMAN",
                    label_reviewer=reviewer,
                    provenance_json=provenance.model_dump(mode="json"),
                )
                .returning(self.t.seed_cases)
            )
            .mappings()
            .one()
        )
        self.audit(
            project_id,
            "SEED_CREATED",
            "seed",
            seed_id,
            original_asset_id=str(original.id),
            normalized_asset_id=str(normalized.id),
            evidence_sha256=original.sha256,
            normalization_policy=image.policy,
        )
        return self.seed_dto(row)

    def _cursor(self, scope: str, entity_id: UUID) -> str:
        payload = canonical_json({"v": 1, "scope": scope, "id": str(entity_id)})
        signature = hmac.digest(
            self.settings.owner_auth_token.get_secret_value().encode(), payload, "sha256"
        )
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _cursor_id(self, scope: str, cursor: str) -> UUID:
        try:
            decoded = base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
            payload, signature = decoded[:-32], decoded[-32:]
            expected = hmac.digest(
                self.settings.owner_auth_token.get_secret_value().encode(), payload, "sha256"
            )
            value = json.loads(payload)
            if (
                not hmac.compare_digest(signature, expected)
                or value["scope"] != scope
                or value["v"] != 1
            ):
                raise ValueError
            return UUID(value["id"])
        except (ValueError, KeyError, TypeError):
            raise ServiceError(ErrorCode.INVALID_INPUT, "Invalid pagination cursor.", 422) from None

    def page[T](
        self,
        table: Table,
        scope: str,
        render: Callable[[RowMapping], T],
        conditions: list[ColumnElement[bool]],
        limit: int,
        cursor: str | None,
    ) -> Page[T]:
        if cursor:
            conditions = [*conditions, table.c.id > self._cursor_id(scope, cursor)]
        rows = (
            self.db.execute(select(table).where(*conditions).order_by(table.c.id).limit(limit + 1))
            .mappings()
            .all()
        )
        return Page(
            items=[render(row) for row in rows[:limit]],
            next_cursor=(
                self._cursor(scope, cast(UUID, rows[limit - 1]["id"]))
                if len(rows) > limit
                else None
            ),
        )

    def projects(self, limit: int, cursor: str | None) -> Page[Project]:
        return self.page(
            self.t.projects,
            "projects:" + self.settings.owner_label,
            self.project_dto,
            [self.t.projects.c.owner_label == self.settings.owner_label],
            limit,
            cursor,
        )

    def seeds(self, project_id: UUID, limit: int, cursor: str | None) -> Page[Seed]:
        self.project(project_id)
        return self.page(
            self.t.seed_cases,
            f"seeds:{project_id}",
            self.seed_dto,
            [self.t.seed_cases.c.project_id == project_id],
            limit,
            cursor,
        )

    def masks(self, seed_id: UUID, limit: int, cursor: str | None) -> Page[Mask]:
        self.seed(seed_id)
        return self.page(
            self.t.seed_masks,
            f"masks:{seed_id}",
            self.mask_dto,
            [self.t.seed_masks.c.seed_id == seed_id, self.t.seed_masks.c.active.is_(True)],
            limit,
            cursor,
        )

    def approve_mask(self, seed_id: UUID, mask_id: UUID, body: ReviewDecision) -> Mask:
        seed = self.seed(seed_id, lock=True)
        mask = self.mask_review.mask(seed_id, mask_id, lock=True)
        if mask["seed_id"] != seed_id:
            raise ServiceError(ErrorCode.NOT_FOUND, "Mask does not belong to this seed.", 404)
        asset = self.asset(mask["mask_asset_id"])
        if asset["project_id"] != seed["project_id"] or asset["kind"] != "MASK":
            raise ServiceError(ErrorCode.FORBIDDEN, "Mask asset is outside this seed project.", 403)
        self.artifacts.read_verified(asset["id"])
        reviewed = body.decision == "VALID"
        row = (
            self.db.execute(
                self.t.seed_masks.update()
                .where(self.t.seed_masks.c.id == mask_id)
                .values(
                    reviewed=reviewed,
                    reviewer=self.settings.owner_label if reviewed else None,
                    reviewed_at=func.now() if reviewed else None,
                )
                .returning(self.t.seed_masks)
            )
            .mappings()
            .one()
        )
        self.mask_review.touch(seed_id)
        self.audit(
            seed["project_id"],
            "MASK_REVIEWED",
            "mask",
            mask_id,
            asset_id=str(asset["id"]),
            evidence_sha256=asset["sha256"],
            prior_reviewed=mask["reviewed"],
            decision=body.decision,
            reason_code=review_text(
                body.reason_code, self.settings.owner_auth_token.get_secret_value()
            ),
            notes=review_text(body.notes, self.settings.owner_auth_token.get_secret_value()),
            reason_sha256=fingerprint(body.reason_code),
            notes_sha256=fingerprint(body.notes),
        )
        return self.mask_dto(row)

    def contract_evidence(self, seed: RowMapping, body: VisualTestContract) -> dict[str, Any]:
        if (
            body.case_id != seed["case_key"]
            or body.prompt != seed["question"]
            or body.expected.model_dump(mode="json") != seed["expected_json"]
        ):
            raise ServiceError(
                ErrorCode.INVALID_INPUT,
                "Contract must preserve the verified seed label and question.",
                422,
            )
        self.mask_review.require_ready(seed["id"])
        source = self.asset(seed["image_asset_id"])
        provenance = SeedProvenance.model_validate(seed["provenance_json"])
        original = self.asset(provenance.original_asset_id)
        if (
            original["project_id"] != seed["project_id"]
            or original["sha256"] != provenance.original_sha256
            or source["id"] != provenance.normalized_asset_id
            or source["sha256"] != provenance.normalized_sha256
            or original["kind"] != "SEED_IMAGE"
            or source["kind"] != "SEED_IMAGE"
        ):
            raise ServiceError(ErrorCode.INVALID_STATE, "Seed provenance integrity check failed.")
        self.artifacts.read_verified(original["id"])
        if source["project_id"] != seed["project_id"]:
            raise ServiceError(ErrorCode.FORBIDDEN, "Seed asset is outside this project.", 403)
        self.artifacts.read_verified(source["id"])
        masks = []
        for asset_id in body.protected.mask_asset_ids:
            asset = self.asset(asset_id)
            if asset["project_id"] != seed["project_id"]:
                raise ServiceError(ErrorCode.FORBIDDEN, "Mask asset is outside this project.", 403)
            mask = (
                self.db.execute(
                    select(self.t.seed_masks)
                    .where(
                        self.t.seed_masks.c.seed_id == seed["id"],
                        self.t.seed_masks.c.mask_asset_id == asset_id,
                        self.t.seed_masks.c.active.is_(True),
                    )
                    .with_for_update(read=True)
                )
                .mappings()
                .one_or_none()
            )
            if mask is None or asset["kind"] != "MASK":
                raise ServiceError(
                    ErrorCode.INVALID_INPUT, "Mask does not belong to this seed.", 422
                )
            if mask["object_label"] != body.protected.label:
                raise ServiceError(
                    ErrorCode.INVALID_INPUT, "Mask label does not match the protected object.", 422
                )
            if not mask["reviewed"] or not mask["reviewer"] or mask["reviewed_at"] is None:
                raise ServiceError(
                    ErrorCode.REVIEW_REQUIRED, "Each exact mask revision requires human approval."
                )
            if (asset["width"], asset["height"]) != (source["width"], source["height"]):
                raise ServiceError(
                    ErrorCode.INVALID_INPUT, "Mask dimensions do not match the seed.", 422
                )
            self.artifacts.read_verified(asset_id)
            masks.append(
                {
                    "asset_id": str(asset_id),
                    "sha256": asset["sha256"],
                    "reviewer": mask["reviewer"],
                    "reviewed_at": mask["reviewed_at"].isoformat(),
                    "object_ordinal": mask["object_ordinal"],
                    "object_label": mask["object_label"],
                    "revision": mask["revision"],
                }
            )
        return {
            "masks": masks,
            "seed": {
                "id": str(seed["id"]),
                "image_asset_id": str(source["id"]),
                "image_sha256": source["sha256"],
                "provenance": seed["provenance_json"],
            },
        }

    def create_contract(self, seed_id: UUID, body: VisualTestContract) -> Contract:
        seed = self.seed(seed_id, lock=True)
        evidence = self.contract_evidence(seed, body)
        version = (
            self.db.scalar(
                select(func.max(self.t.visual_contracts.c.version)).where(
                    self.t.visual_contracts.c.seed_id == seed_id
                )
            )
            or 0
        ) + 1
        document = body.model_dump(mode="json")
        row = (
            self.db.execute(
                self.t.visual_contracts.insert()
                .values(
                    seed_id=seed_id,
                    version=version,
                    schema_version=body.schema_version,
                    contract_json=document,
                    contract_sha256=fingerprint(document),
                    evidence_snapshot_json=evidence,
                )
                .returning(self.t.visual_contracts)
            )
            .mappings()
            .one()
        )
        self.audit(
            seed["project_id"],
            "CONTRACT_CREATED",
            "contract",
            row["id"],
            version=version,
            contract_sha256=row["contract_sha256"],
        )
        return self.contract_dto(row)

    @staticmethod
    def contract_dto(row: RowMapping) -> Contract:
        return Contract(
            id=row["id"],
            seed_id=row["seed_id"],
            version=row["version"],
            contract=VisualTestContract.model_validate(row["contract_json"]),
            contract_sha256=row["contract_sha256"],
            locked_at=row["locked_at"],
        )

    def contract(self, contract_id: UUID) -> RowMapping:
        row = self.row(self.t.visual_contracts, contract_id)
        self.seed(row["seed_id"])
        return row

    def validate_contract(self, body: VisualTestContract) -> ValidationReport:
        seed = (
            self.db.execute(
                select(self.t.seed_cases).where(self.t.seed_cases.c.case_key == body.case_id)
            )
            .mappings()
            .one_or_none()
        )
        if seed is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Seed case was not found.", 404)
        self.project(seed["project_id"])
        self.contract_evidence(seed, body)
        return ValidationReport(
            verdict="VALID",
            verifier_version="contract-evidence-v1",
            checks=[CheckEvidence(code="APPROVED_SEED_MASK_REFERENCES", status="PASS")],
            confidence_note="Contract references are valid; candidate-image validity is evaluated separately.",
            evidence_asset_ids=body.protected.mask_asset_ids,
        )

    def target(self, target_id: UUID, *, lock: bool = False) -> RowMapping:
        row = self.row(self.t.target_configs, target_id, lock=lock)
        self.project(row["project_id"])
        return row

    @staticmethod
    def target_dto(row: RowMapping) -> Target:
        return Target(
            **{
                name: row[name]
                for name in (
                    "id",
                    "project_id",
                    "adapter_key",
                    "model_id",
                    "model_revision",
                    "inference_options",
                    "enabled",
                )
            }
        )

    def create_target(self, project_id: UUID, body: CreateTargetConfig) -> Target:
        self.project(project_id)
        models = {"META_SPARK": {"muse-spark-1.3"}, "LOCAL_FIXTURE": {"LOCAL_FIXTURE"}}
        if body.model_id not in models[body.adapter_key] or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,120}", body.model_revision
        ):
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Choose a curated target model and revision.", 422
            )
        row = (
            self.db.execute(
                self.t.target_configs.insert()
                .values(
                    project_id=project_id,
                    **body.model_dump(mode="json"),
                    provider="META" if body.adapter_key == "META_SPARK" else "FIXTURE",
                    secret_ref="env:MODEL_API_KEY" if body.adapter_key == "META_SPARK" else None,
                )
                .returning(self.t.target_configs)
            )
            .mappings()
            .one()
        )
        self.audit(project_id, "TARGET_CREATED", "target", row["id"], adapter_key=body.adapter_key)
        return self.target_dto(row)

    def targets(self, project_id: UUID, limit: int, cursor: str | None) -> Page[Target]:
        self.project(project_id)
        return self.page(
            self.t.target_configs,
            f"targets:{project_id}",
            self.target_dto,
            [self.t.target_configs.c.project_id == project_id],
            limit,
            cursor,
        )

    @staticmethod
    def experiment_ref(row: RowMapping) -> ExperimentRef:
        return ExperimentRef(
            experiment_id=row["id"],
            state=row["state"],
            links=ExperimentLinks(
                self=f"/api/v1/experiments/{row['id']}",
                events=f"/api/v1/experiments/{row['id']}/events",
            ),
        )

    def start_experiment(
        self,
        body: StartExperiment,
        idempotency_key: str,
        *,
        _evaluation: dict[str, Any] | None = None,
        _experiment_id: UUID | None = None,
    ) -> ExperimentRef:
        self.project(body.project_id, lock=True)
        request = body.model_dump(mode="json")
        if body.search_policy == "ONE_SHOT":
            # Match pre-feedback fingerprints for safe retries across deployment.
            request.pop("search_policy")
        if _evaluation is not None:
            if set(_evaluation) != {"evaluation", "evaluation_identity"} or _experiment_id is None:
                raise ValueError("Internal evaluation requires complete immutable metadata and ID")
            request["evaluation"] = _evaluation
            request["experiment_id"] = str(_experiment_id)
        elif _experiment_id is not None:
            raise ValueError("Explicit IDs are reserved for declared evaluations")
        digest = fingerprint(request)
        previous = (
            self.db.execute(
                select(self.t.experiments).where(
                    self.t.experiments.c.project_id == body.project_id,
                    self.t.experiments.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        if previous is not None:
            if previous["request_fingerprint"] != digest:
                raise IdempotencyConflict("Changed experiment request")
            return self.experiment_ref(previous)
        contract = self.row(self.t.visual_contracts, body.contract_id, lock=True)
        seed = self.seed(contract["seed_id"], lock=True)
        target = self.target(body.target_config_id, lock=True)
        if seed["project_id"] != body.project_id or target["project_id"] != body.project_id:
            raise ServiceError(
                ErrorCode.FORBIDDEN, "Experiment references must belong to its project.", 403
            )
        if not target["enabled"]:
            raise ServiceError(ErrorCode.INVALID_STATE, "Target is disabled.")
        vtc = VisualTestContract.model_validate(contract["contract_json"])
        if fingerprint(vtc.model_dump(mode="json")) != contract["contract_sha256"]:
            raise ServiceError(ErrorCode.INVALID_STATE, "Contract integrity check failed.")
        request_limits, ceiling = body.limits.model_dump(), vtc.limits.model_dump()
        if any(
            Decimal(str(value)) > Decimal(str(ceiling[key]))
            for key, value in request_limits.items()
        ):
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Request limits can only tighten contract limits.", 422
            )
        evidence = self.contract_evidence(seed, vtc)
        if not evidence.get("masks") or not evidence.get("seed"):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Contract lacks approved evidence snapshots."
            )
        for item in evidence["masks"]:
            asset = self.asset(UUID(item["asset_id"]))
            if asset["project_id"] != body.project_id or asset["sha256"] != item["sha256"]:
                raise ServiceError(
                    ErrorCode.FORBIDDEN, "Snapshot asset is outside the project.", 403
                )
            self.artifacts.read_verified(asset["id"])
        self.artifacts.read_verified(seed["image_asset_id"])
        from counterforge_meta.usage import PriceBook

        prices = PriceBook.load(self.settings.pricing_path)
        from counterforge_meta.spark import PLANNER_TEMPLATE

        snapshot = {
            **evidence,
            "search_policy": body.search_policy,
            "contract": vtc.model_dump(mode="json"),
            "contract_sha256": contract["contract_sha256"],
            "limits": request_limits,
            "target": {
                key: target[key]
                for key in (
                    "adapter_key",
                    "provider",
                    "model_id",
                    "model_revision",
                    "prompt_template",
                    "inference_options",
                )
            },
            "verifier_version": "phase04-1",
            "policy_version": "strict-binary-human-gates-1",
            "adapter_version": "target-count-v1",
            "pricing_revision": prices.revision,
            "pricing": {
                "revision": prices.revision,
                "source_url": prices.source_url,
                "checked_at": prices.checked_at,
                "models": prices.models,
            },
            "strategy": {
                "random_seed": body.random_seed
                if body.random_seed is not None
                else uuid4().int & (2**63 - 1),
                "planner_template_revision": "random-loop-v1"
                if body.search_policy == "RANDOM_V1"
                else PLANNER_TEMPLATE,
                "slot_strategy": "seed-plus-slot-v1"
                if body.search_policy == "RANDOM_V1"
                else "ordinal-mod5-zero-random-v1",
                "revision": "feedback-v1"
                if body.search_policy == "FEEDBACK_V1"
                else "random-loop-v1"
                if body.search_policy == "RANDOM_V1"
                else "random-initial-v2"
                if body.mode == "RANDOM_BASELINE"
                else "spark-initial-v1",
                "strength_range": [0, 1],
            },
            "provider_dispatch_enabled": self.settings.provider_mode != "DISABLED",
            "provider_mode": self.settings.provider_mode,
        }
        if _evaluation is not None:
            snapshot.update(_evaluation)
        limits = body.limits
        row = (
            self.db.execute(
                self.t.experiments.insert()
                .values(
                    **({"id": _experiment_id} if _experiment_id is not None else {}),
                    project_id=body.project_id,
                    contract_id=body.contract_id,
                    target_config_id=body.target_config_id,
                    mode=body.mode,
                    state="BASELINE_VALIDATING",
                    config_snapshot=snapshot,
                    max_candidates=limits.max_candidates,
                    max_target_calls=limits.max_target_calls,
                    max_estimated_usd=Decimal(str(limits.max_estimated_usd)),
                    max_runtime_seconds=limits.max_runtime_seconds,
                    started_at=func.now(),
                    idempotency_key=idempotency_key,
                    request_fingerprint=digest,
                )
                .returning(self.t.experiments)
            )
            .mappings()
            .one()
        )
        if contract["locked_at"] is None:
            self.db.execute(
                self.t.visual_contracts.update()
                .where(self.t.visual_contracts.c.id == body.contract_id)
                .values(locked_at=func.now())
            )
            self.audit(
                body.project_id,
                "CONTRACT_LOCKED",
                "contract",
                body.contract_id,
                contract_sha256=contract["contract_sha256"],
            )
        self.db.execute(
            self.t.experiment_events.insert().values(
                experiment_id=row["id"],
                event_type="EXPERIMENT_STARTED",
                payload_json={"state": "BASELINE_VALIDATING"},
            )
        )
        self.audit(
            body.project_id,
            "EXPERIMENT_STARTED",
            "experiment",
            row["id"],
            contract_id=str(body.contract_id),
        )
        from .experiments import queue

        queue(self, row["id"])
        return self.experiment_ref(row)

    def require_valid_candidate(self, mutation_id: UUID) -> Any:
        """Phase05 target-dispatch gate: current sealed report and retained assets."""
        return self.candidates.require_valid_candidate(mutation_id)

    def experiment(self, experiment_id: UUID) -> RowMapping:
        row = self.row(self.t.experiments, experiment_id)
        self.project(row["project_id"])
        return row

    def experiment_dto(self, row: RowMapping) -> ExperimentView:
        usage = self.t.usage_events
        actual = self.db.scalar(
            select(func.coalesce(func.sum(usage.c.actual_usd), 0)).where(
                usage.c.experiment_id == row["id"], usage.c.status == "SETTLED"
            )
        )
        unknown = self.db.scalar(
            select(func.coalesce(func.sum(usage.c.estimated_max_usd), 0)).where(
                usage.c.experiment_id == row["id"], usage.c.status == "UNKNOWN_CHARGE"
            )
        )
        estimated = self.db.scalar(
            select(func.coalesce(func.sum(usage.c.accounted_estimate_usd), 0)).where(
                usage.c.experiment_id == row["id"],
                usage.c.status == "SETTLED",
                usage.c.cost_kind == "ESTIMATED",
            )
        )
        mutations, validations = self.t.mutations, self.t.mutation_validations
        latest = (
            select(validations.c.mutation_id, validations.c.verdict)
            .distinct(validations.c.mutation_id)
            .order_by(
                validations.c.mutation_id, validations.c.revision.desc(), validations.c.id.desc()
            )
            .subquery()
        )
        counts: dict[str, int] = {
            verdict: count
            for verdict, count in self.db.execute(
                select(latest.c.verdict, func.count())
                .select_from(mutations.join(latest, mutations.c.id == latest.c.mutation_id))
                .where(
                    mutations.c.experiment_id == row["id"],
                    mutations.c.minimization_job_id.is_(None),
                )
                .group_by(latest.c.verdict)
            ).all()
        }
        generated = self.db.scalar(
            select(func.count())
            .select_from(mutations)
            .where(
                mutations.c.experiment_id == row["id"],
                mutations.c.image_asset_id.is_not(None),
                mutations.c.minimization_job_id.is_(None),
            )
        )
        observations = self.t.model_observations
        screened = self.db.scalar(
            select(func.count())
            .select_from(
                observations.join(latest, observations.c.mutation_id == latest.c.mutation_id)
            )
            .where(
                observations.c.experiment_id == row["id"],
                observations.c.phase == "SCREENING",
                latest.c.verdict == "VALID",
                observations.c.mutation_id.in_(
                    select(mutations.c.id).where(mutations.c.minimization_job_id.is_(None))
                ),
            )
        )
        stable = self.db.scalar(
            select(func.count())
            .select_from(self.t.findings)
            .where(
                self.t.findings.c.experiment_id == row["id"],
                self.t.findings.c.status == "STABLE",
                self.t.findings.c.mutation_id.in_(
                    select(mutations.c.id).where(mutations.c.minimization_job_id.is_(None))
                ),
            )
        )
        from .accounting import Budget
        from .planner_history import planner_history, planning_batches

        return ExperimentView(
            search_policy=row["config_snapshot"].get("search_policy", "ONE_SHOT"),
            planner_history=planner_history(self, row["id"]),
            planning_batches=planning_batches(self, row["id"]),
            id=row["id"],
            project_id=row["project_id"],
            contract_id=row["contract_id"],
            target_config_id=row["target_config_id"],
            state=row["state"],
            limits=Limits(
                max_candidates=row["max_candidates"],
                max_target_calls=row["max_target_calls"],
                max_estimated_usd=float(row["max_estimated_usd"]),
                max_runtime_seconds=row["max_runtime_seconds"],
            ),
            metrics=Metrics(
                generated_candidates=generated or 0,
                valid_candidates=counts.get("VALID", 0),
                invalid_candidates=counts.get("INVALID", 0),
                review_required_candidates=counts.get("REVIEW_REQUIRED", 0),
                valid_screening_calls=screened or 0,
                total_target_calls=row["calls_consumed"],
                stable_findings=stable or 0,
                actual_provider_usage_usd=float(actual or 0),
                conservative_estimate_usd=float(row["reserved_usd"] + (estimated or 0)),
                unresolved_charges_usd=float(unknown or 0),
            ),
            stop_reason=row["stop_reason"],
            mode=row["mode"],
            synthetic=row["config_snapshot"].get("provider_mode") == "FIXTURE",
            started_at=row["started_at"],
            elapsed_seconds=max(
                0,
                (
                    (row["ended_at"] or self.db.scalar(select(func.clock_timestamp())))
                    - row["started_at"]
                ).total_seconds(),
            )
            if row["started_at"]
            else 0,
            resume_blocked_reason=(
                "Resolve retained provider outcomes or unknown charges before resuming."
                if row["state"] == "PAUSED"
                and (
                    row["checkpoint_json"].get("unresolved_result")
                    or self.db.scalar(
                        select(usage.c.id)
                        .where(
                            usage.c.experiment_id == row["id"], usage.c.status == "UNKNOWN_CHARGE"
                        )
                        .limit(1)
                    )
                )
                else None
            ),
            baseline_completed=self.db.scalar(
                select(func.count())
                .select_from(observations)
                .where(
                    observations.c.experiment_id == row["id"],
                    observations.c.phase == "SEED_BASELINE",
                )
            )
            or 0,
            spent_usd=float(row["spent_usd"]),
            reserved_usd=float(row["reserved_usd"]),
            reserved_target_calls=Budget(self.uow)._pending(row["id"])[0],
        )

    def job_dto(self, row: RowMapping) -> JobRef:
        events = (
            self.db.execute(
                select(self.t.usage_events).where(
                    self.t.usage_events.c.background_job_id == row["id"]
                )
            )
            .mappings()
            .all()
        )
        usage = JobUsage(
            actual_usd=float(
                sum(
                    (e["actual_usd"] or Decimal(0))
                    for e in events
                    if e["status"] == "SETTLED" and e["cost_kind"] == "ACTUAL"
                )
            ),
            estimated_usd=float(
                sum(
                    e["accounted_estimate_usd"]
                    for e in events
                    if e["status"] == "SETTLED" and e["cost_kind"] == "ESTIMATED"
                )
            ),
            unresolved_usd=float(
                sum(e["estimated_max_usd"] for e in events if e["status"] == "UNKNOWN_CHARGE")
            ),
            reserved_usd=float(
                sum(e["estimated_max_usd"] for e in events if e["status"] == "RESERVED")
            ),
        )
        result = row["result_json"]
        parsed_result = JobResult.model_validate(result) if result and "outcome" in result else None
        if parsed_result is not None:
            parsed_result = parsed_result.model_copy(
                update={
                    "evidence_asset_ids": sorted(
                        {
                            *parsed_result.evidence_asset_ids,
                            *(
                                e["raw_response_asset_id"]
                                for e in events
                                if e["raw_response_asset_id"] is not None
                            ),
                        },
                        key=str,
                    )
                }
            )
        return JobRef(
            job_id=row["id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            state=row["state"],
            links=JobLinks(self=f"/api/v1/jobs/{row['id']}"),
            error_code=row["error_code"],
            usage=usage,
            result=parsed_result,
        )

    def job(self, job_id: UUID) -> RowMapping:
        row = self.row(self.t.background_jobs, job_id)
        self.project(row["project_id"])
        return row

    def segment(self, seed_id: UUID, body: SegmentationRequest, key: str | None) -> JobRef:
        """Queue durable intent only. Stated job limits are the dispatch authorization:
        a job without them may wait here, but accounting refuses to dispatch it."""

        seed = self.seed(seed_id, lock=True)
        job_id = self.uow.background_jobs.enqueue(
            project_id=seed["project_id"],
            kind="SEGMENT",
            request_json={
                "seed_id": str(seed_id),
                "mask_generation": seed["mask_generation"],
                **body.model_dump(mode="json", exclude_none=True),
            },
            idempotency_key=key,
        )
        return self.job_dto(self.job(job_id))

    def check_target(
        self, target_id: UUID, key: str | None, body: TargetCheckRequest | None = None
    ) -> JobRef:
        target = self.target(target_id)
        request: dict[str, Any] = {
            "target_config_id": str(target_id),
            "target_snapshot": {
                name: target[name]
                for name in (
                    "adapter_key",
                    "model_id",
                    "model_revision",
                    "prompt_template",
                    "inference_options",
                )
            },
        }
        if body is not None and body.seed_id is not None:
            seed = self.seed(body.seed_id)
            if seed["project_id"] != target["project_id"]:
                raise ServiceError(ErrorCode.FORBIDDEN, "Target seed is outside this project.", 403)
            request["seed_id"] = str(body.seed_id)
        if body is not None and body.limits is not None:
            request["limits"] = body.limits.model_dump(mode="json")
        job_id = self.uow.background_jobs.enqueue(
            project_id=target["project_id"],
            kind="TARGET_CHECK",
            request_json=request,
            idempotency_key=key,
        )
        return self.job_dto(self.job(job_id))

    def finding(self, finding_id: UUID) -> RowMapping:
        row = self.row(self.t.findings, finding_id)
        self.experiment(row["experiment_id"])
        return row

    def finding_dto(self, row: RowMapping, *, include_evidence: bool = False) -> Finding:
        experiment = self.experiment(row["experiment_id"])
        mutation = self.row(self.t.mutations, row["mutation_id"])
        contract = self.contract(experiment["contract_id"])
        seed = self.seed(contract["seed_id"])
        target = self.target(experiment["target_config_id"])
        if (
            mutation["experiment_id"] != experiment["id"]
            or target["project_id"] != experiment["project_id"]
        ):
            raise ServiceError(
                ErrorCode.FORBIDDEN, "Finding references are outside its experiment.", 403
            )
        if mutation["image_asset_id"] is None:
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Finding image evidence is incomplete.")
        for asset_id in (seed["image_asset_id"], mutation["image_asset_id"]):
            if self.asset(asset_id)["project_id"] != experiment["project_id"]:
                raise ServiceError(
                    ErrorCode.FORBIDDEN, "Finding assets are outside its project.", 403
                )
        validation = (
            self.db.execute(
                select(self.t.mutation_validations)
                .where(self.t.mutation_validations.c.mutation_id == mutation["id"])
                .order_by(
                    self.t.mutation_validations.c.revision.desc(),
                    self.t.mutation_validations.c.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if validation is None or (row["status"] == "STABLE" and validation["verdict"] != "VALID"):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Finding validity evidence is incomplete."
            )
        observations = list(
            self.db.scalars(
                select(self.t.model_observations.c.id)
                .where(
                    self.t.model_observations.c.experiment_id == experiment["id"],
                    self.t.model_observations.c.mutation_id == mutation["id"],
                    self.t.model_observations.c.target_config_id == target["id"],
                    self.t.model_observations.c.phase == "CONFIRMATION",
                )
                .order_by(self.t.model_observations.c.attempt_no)
            )
        )
        pinned_target = experiment["config_snapshot"]["target"]
        evidence = None
        if include_evidence and "report" in validation["evidence_json"]:
            try:
                evidence = self.finding_evidence(row["id"])
            except ServiceError as exc:
                if exc.status != 409:
                    raise
        # Build from authoritative evidence columns; arbitrary summary/provider JSON is private.
        return Finding(
            id=row["id"],
            mutation_id=row["mutation_id"],
            status=row["status"],
            evidence=evidence,
            ground_truth={"value": contract["contract_json"]["expected"]["value"]},
            model={
                "adapter": pinned_target["adapter_key"],
                "model_id": pinned_target["model_id"],
                "revision": pinned_target["model_revision"],
            },
            mutation={"family": mutation["family"], "strength": float(mutation["strength"])},
            validation={
                "verdict": validation["verdict"],
                "verifier_version": validation["verifier_version"],
                "evidence_ids": [],
            },
            confirmation={
                "incorrect": row["confirmation_failures"],
                "attempts": row["confirmation_attempts"],
                "raw_observation_ids": observations,
            },
            assets={
                "original_image_id": seed["image_asset_id"],
                "mutated_image_id": mutation["image_asset_id"],
            },
            demo_approved=row["demo_approved_at"] is not None,
            demo_approved_by=row["demo_approved_by"],
            demo_approved_at=row["demo_approved_at"],
            qualification=f"{row['confirmation_failures']}/{row['confirmation_attempts']} incorrect under recorded settings; no claim of universal reproducibility.",
        )

    def findings(
        self, experiment_id: UUID, limit: int, cursor: str | None, status: str | None
    ) -> Page[Finding]:
        self.experiment(experiment_id)
        conditions = [self.t.findings.c.experiment_id == experiment_id]
        if status:
            conditions.append(self.t.findings.c.status == status)
        return self.page(
            self.t.findings,
            f"findings:{experiment_id}:{status}",
            self.finding_dto,
            conditions,
            limit,
            cursor,
        )

    def finding_evidence(self, finding_id: UUID) -> "FindingEvidence":
        from .exports import finding_evidence

        return finding_evidence(self, finding_id)

    @staticmethod
    def export_ref(export_id: UUID, state: str, **details: Any) -> ExportRef:
        return ExportRef(
            export_id=export_id,
            state=state,
            links=ExportLinks(
                self=f"/api/v1/exports/{export_id}",
                download=f"/api/v1/exports/{export_id}/download" if state == "READY" else None,
            ),
            **details,
        )

    def get_export(self, export_id: UUID, *, verify_blobs: bool = True) -> ExportRef:
        from counterforge_regression.manifest import RegressionManifest

        from .artifacts import IntegrityError

        row = (
            self.db.execute(
                select(self.t.regression_exports).where(self.t.regression_exports.c.id == export_id)
            )
            .mappings()
            .one_or_none()
        )
        job = self.job(export_id)
        if job["kind"] != "EXPORT" or job["finding_id"] is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Export was not found.", 404)
        f = self.finding(job["finding_id"])
        e = self.experiment(f["experiment_id"])
        if job["project_id"] != e["project_id"] or job["experiment_id"] != e["id"]:
            raise ServiceError(
                ErrorCode.FORBIDDEN, "Export references are outside the finding.", 403
            )
        details = dict(
            finding_id=f["id"],
            project_id=e["project_id"],
            job_id=job["id"],
            provenance="LOCAL_FIXTURE"
            if e["config_snapshot"].get("provider_mode") == "FIXTURE"
            else "REAL_PROVIDER",
            error_code=job["error_code"],
        )
        if row is not None:
            if row["finding_id"] != f["id"]:
                raise ServiceError(ErrorCode.FORBIDDEN, "Export finding reference differs.", 403)
            state = row["status"]
            bundle, manifest = (
                self.asset(row["bundle_asset_id"]),
                self.asset(row["manifest_asset_id"]),
            )
            if bundle["project_id"] != e["project_id"] or manifest["project_id"] != e["project_id"]:
                raise ServiceError(
                    ErrorCode.FORBIDDEN, "Export asset references are outside the finding.", 403
                )
            try:
                frozen = RegressionManifest.model_validate_json(
                    self.artifacts.read_verified(manifest["id"])
                )
                if verify_blobs:
                    ev = self.finding_evidence(f["id"])
                    current = (
                        ev.export_eligible
                        and frozen.invariant_verification.report_sha256
                        == ev.validation.report_sha256
                    )
                else:
                    from .exports import registered_export_is_current

                    current = registered_export_is_current(self, f, frozen)
                if not current:
                    state = "REVOKED"
                if frozen.finding_id != f["id"] or frozen.export_job_id != export_id:
                    state = "REVOKED"
                if verify_blobs:
                    self.artifacts.read_verified(bundle["id"])
            except (ServiceError, ValueError, IntegrityError):
                state = "REVOKED"
            result = self.export_ref(
                export_id,
                state,
                **details,
                bundle_sha256=bundle["sha256"],
                manifest_sha256=manifest["sha256"],
                runner_version=row["runner_version"],
            )
            if not verify_blobs:
                result.links.download = None
            return result
        return self.export_ref(
            export_id,
            "FAILED" if job["state"] in {"REVIEW_REQUIRED", "SUCCEEDED"} else job["state"],
            **details,
        )

    def request_export(self, finding_id: UUID, key: str) -> ExportRef:
        finding = self.finding(finding_id)
        self.candidates.row(finding["mutation_id"], lock=True)
        ev = self.finding_evidence(finding_id)
        if not ev.export_eligible:
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "Export requires complete current stable reviewed evidence.",
            )
        experiment = self.experiment(finding["experiment_id"])
        job_id = self.uow.background_jobs.enqueue(
            project_id=experiment["project_id"],
            experiment_id=experiment["id"],
            finding_id=finding_id,
            kind="EXPORT",
            request_json={
                "finding_id": str(finding_id),
                "validation_sha256": ev.validation.report_sha256,
            },
            idempotency_key=key,
        )
        self.audit(
            experiment["project_id"], "EXPORT_REQUESTED", "job", job_id, finding_id=str(finding_id)
        )
        return self.get_export(job_id)

    def complete_export(
        self,
        job_id: UUID,
        bundle_id: UUID,
        manifest_id: UUID,
        runner_version: str,
        *,
        claim_token: UUID,
        delivery_no: int,
    ) -> ExportRef:
        job = self.row(self.t.background_jobs, job_id, lock=True)
        self.project(job["project_id"])
        if (
            job["kind"] != "EXPORT"
            or job["finding_id"] is None
            or job["state"] != "RUNNING"
            or job["claim_token"] != claim_token
            or job["delivery_no"] != delivery_no
            or job["lease_expires_at"] is None
            or job["lease_expires_at"] <= self.db.scalar(select(func.now()))
        ):
            raise ServiceError(ErrorCode.INVALID_STATE, "Export job cannot complete in this state.")
        finding = self.finding(job["finding_id"])
        # Match review's mutation -> finding lock order (its trigger updates finding).
        self.candidates.row(finding["mutation_id"], lock=True)
        finding = self.row(self.t.findings, finding["id"], lock=True)
        experiment = self.experiment(finding["experiment_id"])
        if (
            experiment["project_id"] != job["project_id"]
            or job["experiment_id"] != experiment["id"]
        ):
            raise ServiceError(
                ErrorCode.FORBIDDEN, "Export job references are outside its project.", 403
            )
        for asset_id, kind, media in (
            (bundle_id, "REGRESSION_ZIP", "application/zip"),
            (manifest_id, "MANIFEST", "application/json"),
        ):
            asset = self.asset(asset_id)
            if (
                asset["project_id"] != job["project_id"]
                or asset["kind"] != kind
                or asset["media_type"] != media
            ):
                raise ServiceError(
                    ErrorCode.FORBIDDEN, "Export assets are outside the authorized evidence.", 403
                )
            self.artifacts.read_verified(asset_id)
        import io
        from zipfile import ZipFile

        from counterforge_regression.bundle import verify_bundle

        from .exports import collect_export, configured_secrets

        self.candidates.row(finding["mutation_id"], lock=True)
        snapshot = collect_export(self, finding["id"], job_id)
        manifest_bytes = self.artifacts.read_verified(manifest_id)
        bundle_bytes = self.artifacts.read_verified(bundle_id)
        verify_bundle(bundle_bytes, forbidden_values=configured_secrets(self.settings))
        with ZipFile(io.BytesIO(bundle_bytes)) as archive:
            if archive.read("manifest.json") != manifest_bytes:
                raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Export manifest and ZIP differ.")
            from counterforge_regression.manifest import RegressionManifest

            manifest = RegressionManifest.model_validate_json(manifest_bytes)
            if manifest.model_dump(exclude={"sha256"}) != snapshot.manifest.model_dump(
                exclude={"sha256"}
            ) or any(archive.read(name) != data for name, data in snapshot.evidence.items()):
                raise ServiceError(
                    ErrorCode.REVIEW_REQUIRED,
                    "Export bundle evidence differs from current finding.",
                )
        self.db.execute(
            self.t.regression_exports.insert().values(
                id=job_id,
                finding_id=job["finding_id"],
                bundle_asset_id=bundle_id,
                manifest_asset_id=manifest_id,
                runner_version=runner_version,
                status="READY",
            )
        )
        self.db.execute(
            self.t.background_jobs.update()
            .where(self.t.background_jobs.c.id == job_id)
            .values(
                state="SUCCEEDED",
                finished_at=func.now(),
                updated_at=func.now(),
                result_json={"export_id": str(job_id)},
                lease_expires_at=None,
            )
        )
        self.audit(job["project_id"], "EXPORT_COMPLETED", "export", job_id)
        return self.get_export(job_id)

    def download_export(self, export_id: UUID) -> bytes:
        if self.get_export(export_id).state != "READY":
            raise ServiceError(ErrorCode.INVALID_STATE, "Export bundle is not ready.")
        export = self.row(self.t.regression_exports, export_id)
        finding = self.finding(export["finding_id"])
        experiment = self.experiment(finding["experiment_id"])
        asset = self.asset(export["bundle_asset_id"])
        if asset["project_id"] != experiment["project_id"] or asset["kind"] != "REGRESSION_ZIP":
            raise ServiceError(ErrorCode.FORBIDDEN, "Export asset is outside the project.", 403)
        data = self.artifacts.read_verified(asset["id"])
        self.audit(experiment["project_id"], "EXPORT_DOWNLOADED", "export", export_id)
        return data

    def regressions(self, limit: int, cursor: str | None) -> Page[ExportRef]:
        exports, findings, experiments, projects, jobs = (
            self.t.regression_exports,
            self.t.findings,
            self.t.experiments,
            self.t.projects,
            self.t.background_jobs,
        )
        completed = (
            select(exports.c.id.label("id"), exports.c.status.label("state"))
            .select_from(
                exports.join(findings, findings.c.id == exports.c.finding_id)
                .join(experiments, experiments.c.id == findings.c.experiment_id)
                .join(projects, projects.c.id == experiments.c.project_id)
            )
            .where(projects.c.owner_label == self.settings.owner_label)
        )
        pending = (
            select(
                jobs.c.id,
                case(
                    (
                        jobs.c.state.in_(["FAILED", "REVIEW_REQUIRED", "SUCCEEDED"]),
                        literal("FAILED"),
                    ),
                    else_=jobs.c.state,
                ).label("state"),
            )
            .select_from(jobs.join(projects, projects.c.id == jobs.c.project_id))
            .where(
                projects.c.owner_label == self.settings.owner_label,
                jobs.c.kind == "EXPORT",
                ~select(exports.c.id).where(exports.c.id == jobs.c.id).exists(),
            )
        )
        combined = union_all(completed, pending).subquery()
        scope = "regressions:" + self.settings.owner_label
        query = select(combined)
        if cursor:
            query = query.where(combined.c.id > self._cursor_id(scope, cursor))
        rows = self.db.execute(query.order_by(combined.c.id).limit(limit + 1)).mappings().all()
        return Page(
            items=[self.get_export(row["id"], verify_blobs=False) for row in rows[:limit]],
            next_cursor=self._cursor(scope, rows[limit - 1]["id"]) if len(rows) > limit else None,
        )
