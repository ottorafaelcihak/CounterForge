"""Current mask drafts, append-only retired versions and scoped human review."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from counterforge_contracts.requests import AcknowledgeMaskMismatch
from counterforge_contracts.responses import Mask, MaskReviewState, MaskRevision, Page
from counterforge_contracts.versions import ErrorCode
from sqlalchemy import func, select, true
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.dml import Insert, Update

from .db.repositories import IdempotencyConflict
from .errors import ServiceError
from .masks import decode_mask
from .redaction import review_text

if TYPE_CHECKING:
    from .service import Service


class MaskReview:
    def __init__(self, service: Service) -> None:
        self.s = service
        self.db, self.t = service.db, service.t

    def mask(
        self, seed_id: UUID, mask_id: UUID, *, lock: bool = False, active: bool = True
    ) -> RowMapping:
        self.s.seed(seed_id, lock=lock)
        query = select(self.t.seed_masks).where(
            self.t.seed_masks.c.id == mask_id, self.t.seed_masks.c.seed_id == seed_id
        )
        if active:
            query = query.where(self.t.seed_masks.c.active.is_(True))
        if lock:
            query = query.with_for_update()
        row = self.db.execute(query).mappings().one_or_none()
        if row is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Mask was not found for this seed.", 404)
        return row

    def touch(self, seed_id: UUID) -> None:
        self.db.execute(
            self.t.seed_cases.update()
            .where(self.t.seed_cases.c.id == seed_id)
            .values(mask_generation=self.t.seed_cases.c.mask_generation + 1)
        )

    def current(self, seed_id: UUID) -> list[RowMapping]:
        return list(
            self.db.execute(
                select(self.t.seed_masks)
                .where(self.t.seed_masks.c.seed_id == seed_id, self.t.seed_masks.c.active.is_(True))
                .order_by(self.t.seed_masks.c.object_ordinal)
            ).mappings()
        )

    def state(self, seed_id: UUID) -> MaskReviewState:
        from .service import fingerprint

        seed = self.s.seed(seed_id)
        current = self.current(seed_id)
        source = seed["segmentation_json"]
        digest = fingerprint(
            {
                "masks": [
                    [
                        str(row["id"]),
                        str(row["mask_asset_id"]),
                        row["revision"],
                        row["object_label"],
                    ]
                    for row in current
                ],
                "generation": seed["mask_generation"],
                "source_job_id": source.get("job_id"),
            }
        )
        expected = int(seed["expected_json"]["value"])
        detected = source.get("detected_count")
        if type(detected) is not int or detected < 0:
            detected = None
        outcome = source.get("outcome")
        mismatch = detected is not None and detected != expected
        incomplete = outcome is not None and outcome != "COMPLETED"
        approved = sum(
            bool(row["reviewed"]) and row["reviewer"] is not None and row["reviewed_at"] is not None
            for row in current
        )
        ack = (
            self.db.scalar(
                select(self.t.seed_mask_acknowledgements.c.id).where(
                    self.t.seed_mask_acknowledgements.c.seed_id == seed_id,
                    self.t.seed_mask_acknowledgements.c.mask_set_sha256 == digest,
                )
            )
            is not None
        )
        blocking = []
        if not 1 <= expected <= 16:
            blocking.append("UNSUPPORTED_COUNT")
        if len(current) != expected:
            blocking.append("MASK_COUNT_MISMATCH")
        if approved != len(current):
            blocking.append("MASK_REVIEW_REQUIRED")
        if (mismatch or incomplete) and not ack:
            blocking.append(
                "SEGMENTATION_REVIEW_REQUIRED" if incomplete else "COUNT_MISMATCH_UNACKNOWLEDGED"
            )
        return MaskReviewState(
            seed_id=seed_id,
            expected_count=expected,
            detected_count=detected,
            draft_count=len(current),
            approved_count=approved,
            mismatch=mismatch,
            acknowledged=ack,
            mask_set_sha256=digest,
            segmentation_outcome=outcome,
            blocking=blocking,
            can_create_contract=not blocking,
            synthetic=source.get("synthetic") is True,
        )

    def require_ready(self, seed_id: UUID) -> None:
        if not self.state(seed_id).can_create_contract:
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "Correct and approve distinct masks, then acknowledge any segmentation discrepancy.",
            )

    def acknowledge(self, seed_id: UUID, body: AcknowledgeMaskMismatch) -> MaskReviewState:
        seed = self.s.seed(seed_id, lock=True)
        state = self.state(seed_id)
        if body.human_count != state.expected_count or body.detected_count != state.detected_count:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Acknowledgement must match the recorded counts.", 422
            )
        if (
            state.draft_count != state.expected_count
            or state.approved_count != state.expected_count
        ):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "Every human-corrected instance must have a distinct approved mask.",
            )
        notes = review_text(body.notes, self.s.settings.owner_auth_token.get_secret_value())
        if not state.acknowledged:
            self.db.execute(
                self.t.seed_mask_acknowledgements.insert().values(
                    seed_id=seed_id,
                    mask_set_sha256=state.mask_set_sha256,
                    detected_count=state.detected_count,
                    human_count=state.expected_count,
                    corrected_count=state.draft_count,
                    actor=self.s.settings.owner_label,
                    notes=notes,
                )
            )
            self.s.audit(
                seed["project_id"],
                "MASK_DISCREPANCY_ACKNOWLEDGED",
                "seed",
                seed_id,
                mask_set_sha256=state.mask_set_sha256,
                detected_count=state.detected_count,
                human_count=state.expected_count,
                notes=notes,
                source_job_id=seed["segmentation_json"].get("job_id"),
            )
        return self.state(seed_id)

    def archive(self, row: RowMapping) -> None:
        asset = self.s.asset(row["mask_asset_id"])
        self.db.execute(
            self.t.seed_mask_revisions.insert().values(
                mask_id=row["id"],
                seed_id=row["seed_id"],
                revision=row["revision"],
                mask_asset_id=row["mask_asset_id"],
                mask_sha256=asset["sha256"],
                object_label=row["object_label"],
                object_ordinal=row["object_ordinal"],
                origin=row["origin"],
                active=row["active"],
                geometry_json=row["geometry_json"],
                reviewed=row["reviewed"],
                reviewer=row["reviewer"],
                reviewed_at=row["reviewed_at"],
                actor=row["revision_actor"],
                notes=row["revision_notes"],
                created_at=row["revision_created_at"],
            )
        )

    def _replay(self, seed_id: UUID, key: str | None, scope: str, request_sha: str) -> Mask | None:
        from .service import fingerprint

        if key is None:
            return None
        row = (
            self.db.execute(
                select(self.t.mask_edit_requests).where(
                    self.t.mask_edit_requests.c.seed_id == seed_id,
                    self.t.mask_edit_requests.c.key_sha256 == fingerprint(key),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["scope"] != scope or row["request_sha256"] != request_sha:
            raise IdempotencyConflict("Changed mask edit request")
        return Mask.model_validate(row["response_json"])

    def _remember(
        self, seed_id: UUID, key: str | None, scope: str, request_sha: str, value: Mask
    ) -> None:
        from .service import fingerprint

        if key is not None:
            self.db.execute(
                self.t.mask_edit_requests.insert().values(
                    seed_id=seed_id,
                    key_sha256=fingerprint(key),
                    scope=scope,
                    request_sha256=request_sha,
                    response_json=value.model_dump(mode="json"),
                )
            )

    def write(
        self,
        seed_id: UUID,
        data: bytes,
        *,
        notes: str,
        mask_id: UUID | None = None,
        object_label: str | None = None,
        key: str | None = None,
    ) -> Mask:
        from .service import fingerprint

        seed = self.s.seed(seed_id, lock=True)
        scope = f"revise:{mask_id}" if mask_id else "add"
        digest = fingerprint(
            {
                "scope": scope,
                "data_sha256": __import__("hashlib").sha256(data).hexdigest(),
                "notes": notes,
                "label": object_label,
            }
        )
        replay = self._replay(seed_id, key, scope, digest)
        if replay is not None:
            return replay
        old = self.mask(seed_id, mask_id, lock=True) if mask_id else None
        label = old["object_label"] if old is not None else (object_label or "").strip()
        if not 2 <= len(label) <= 100 or not 1 <= len(notes.strip()) <= 2000:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "A mask label and correction notes are required.", 422
            )
        asset = self.s.asset(seed["image_asset_id"])
        binary = decode_mask(data, width=asset["width"], height=asset["height"])
        normalized = self.s.artifacts.put(
            seed["project_id"],
            binary.png,
            "MASK",
            "image/png",
            width=binary.width,
            height=binary.height,
            metadata=binary.geometry(),
        )
        if old is not None and normalized.sha256 == self.s.asset(old["mask_asset_id"])["sha256"]:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Mask pixels are unchanged; approval is retained.", 422
            )
        duplicate = self.db.scalar(
            select(self.t.seed_masks.c.id).where(
                self.t.seed_masks.c.seed_id == seed_id,
                self.t.seed_masks.c.mask_asset_id == normalized.id,
                self.t.seed_masks.c.active.is_(True),
                self.t.seed_masks.c.id != mask_id if mask_id else true(),
            )
        )
        if duplicate is not None:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Each instance requires a distinct mask.", 422
            )
        safe_notes = review_text(notes, self.s.settings.owner_auth_token.get_secret_value())
        values: dict[str, Any] = {
            "mask_asset_id": normalized.id,
            "object_label": label,
            "geometry_json": binary.geometry(),
            "origin": "HUMAN",
            "reviewed": False,
            "reviewer": None,
            "reviewed_at": None,
            "active": True,
            "revision_notes": safe_notes,
            "revision_actor": self.s.settings.owner_label,
            "revision_created_at": func.now(),
            "updated_at": func.now(),
        }
        statement: Insert | Update
        if old is None:
            if len(self.current(seed_id)) >= 16:
                raise ServiceError(
                    ErrorCode.INVALID_INPUT,
                    "At most sixteen active instance masks are supported.",
                    422,
                )
            maximum = self.db.scalar(
                select(func.max(self.t.seed_masks.c.object_ordinal)).where(
                    self.t.seed_masks.c.seed_id == seed_id
                )
            )
            ordinal = (maximum if maximum is not None else -1) + 1
            statement = self.t.seed_masks.insert().values(
                seed_id=seed_id, object_ordinal=ordinal, **values
            )
        else:
            self.archive(old)
            statement = (
                self.t.seed_masks.update()
                .where(self.t.seed_masks.c.id == mask_id)
                .values(revision=old["revision"] + 1, **values)
            )
        row = self.db.execute(statement.returning(self.t.seed_masks)).mappings().one()
        self.touch(seed_id)
        self.s.audit(
            seed["project_id"],
            "MASK_REVISED" if old is not None else "MASK_ADDED",
            "mask",
            row["id"],
            seed_id=str(seed_id),
            old_sha256=self.s.asset(old["mask_asset_id"])["sha256"] if old is not None else None,
            new_sha256=normalized.sha256,
            revision=row["revision"],
            geometry=binary.geometry(),
            notes=safe_notes,
        )
        value = self.s.mask_dto(row)
        self._remember(seed_id, key, scope, digest, value)
        return value

    def retire(self, seed_id: UUID, mask_id: UUID, notes: str) -> Mask:
        seed = self.s.seed(seed_id, lock=True)
        old = self.mask(seed_id, mask_id, lock=True)
        self.archive(old)
        safe_notes = review_text(notes, self.s.settings.owner_auth_token.get_secret_value())
        row = (
            self.db.execute(
                self.t.seed_masks.update()
                .where(self.t.seed_masks.c.id == mask_id)
                .values(
                    active=False,
                    reviewed=False,
                    reviewer=None,
                    reviewed_at=None,
                    revision=old["revision"] + 1,
                    revision_actor=self.s.settings.owner_label,
                    revision_notes=safe_notes,
                    revision_created_at=func.now(),
                    updated_at=func.now(),
                )
                .returning(self.t.seed_masks)
            )
            .mappings()
            .one()
        )
        self.touch(seed_id)
        self.s.audit(
            seed["project_id"],
            "MASK_RETIRED",
            "mask",
            mask_id,
            notes=safe_notes,
            asset_id=str(old["mask_asset_id"]),
        )
        return self.s.mask_dto(row)

    def history(
        self, seed_id: UUID, mask_id: UUID, limit: int, cursor: str | None
    ) -> Page[MaskRevision]:
        current = self.mask(seed_id, mask_id, active=False)
        scope = f"mask-history:{seed_id}:{mask_id}"
        after = self.s._cursor_id(scope, cursor).int if cursor else 0
        rows = list(
            self.db.execute(
                select(self.t.seed_mask_revisions)
                .where(
                    self.t.seed_mask_revisions.c.mask_id == mask_id,
                    self.t.seed_mask_revisions.c.revision > after,
                )
                .order_by(self.t.seed_mask_revisions.c.revision)
                .limit(limit + 1)
            ).mappings()
        )
        items = []
        for row in rows:
            mask = self.s.mask_dto({**row, "id": mask_id})
            items.append(
                MaskRevision(
                    **mask.model_dump(),
                    actor=row["actor"],
                    notes=row["notes"],
                    created_at=row["created_at"],
                    retired_at=row["retired_at"],
                )
            )
        if current["revision"] > after:
            items.append(
                MaskRevision(
                    **self.s.mask_dto(current).model_dump(),
                    actor=current["revision_actor"],
                    notes=current["revision_notes"],
                    created_at=current["revision_created_at"],
                    retired_at=None if current["active"] else current["updated_at"],
                )
            )
        items.sort(key=lambda item: item.revision)
        next_cursor = (
            self.s._cursor(scope, UUID(int=items[limit - 1].revision))
            if len(items) > limit
            else None
        )
        return Page[MaskRevision](items=items[:limit], next_cursor=next_cursor)

    def apply_proposals(
        self,
        seed_id: UUID,
        *,
        job_id: UUID,
        expected_generation: int,
        proposals: list[tuple[UUID, dict[str, Any]]],
        object_label: str,
        outcome: str,
        diagnostics: list[str],
        synthetic: bool = False,
    ) -> bool:
        """Install an unapproved proposal only if the queued human draft is unchanged.

        Caller must hold the job's dispatch fence and commit job completion in this UOW.
        Late results remain in job evidence and cannot replace a human correction.
        """
        seed = self.s.seed(seed_id, lock=True)
        if seed["mask_generation"] != expected_generation:
            return False
        if len(proposals) > 16 or len({item[0] for item in proposals}) != len(proposals):
            raise ServiceError(ErrorCode.INVALID_INPUT, "Invalid distinct mask proposals.", 422)
        source = self.s.asset(seed["image_asset_id"])
        for asset_id, _geometry in proposals:
            asset = self.s.asset(asset_id)
            if (
                asset["project_id"] != seed["project_id"]
                or asset["kind"] != "MASK"
                or (asset["width"], asset["height"]) != (source["width"], source["height"])
            ):
                raise ServiceError(
                    ErrorCode.INVALID_INPUT, "Mask proposal geometry is invalid.", 422
                )
        previous = list(
            self.db.execute(
                select(self.t.seed_masks)
                .where(self.t.seed_masks.c.seed_id == seed_id)
                .order_by(self.t.seed_masks.c.object_ordinal)
            ).mappings()
        )
        for row in previous:
            if row["active"]:
                self.archive(row)
        # Release the partial uniqueness slots before installing reordered masks.
        self.db.execute(
            self.t.seed_masks.update()
            .where(self.t.seed_masks.c.seed_id == seed_id)
            .values(active=False)
        )
        # Unused active rows now represent a new retired revision. The archived
        # revision remains immutable and must never be archived a second time.
        for retired_row in previous[len(proposals) :]:
            if retired_row["active"]:
                self.db.execute(
                    self.t.seed_masks.update()
                    .where(self.t.seed_masks.c.id == retired_row["id"])
                    .values(
                        revision=retired_row["revision"] + 1,
                        reviewed=False,
                        reviewer=None,
                        reviewed_at=None,
                        revision_actor="segmentation-worker",
                        revision_notes="Retired by a replacement segmentation proposal.",
                        revision_created_at=func.now(),
                        updated_at=func.now(),
                    )
                )
        for ordinal, (asset_id, geometry) in enumerate(proposals):
            old = previous[ordinal] if ordinal < len(previous) else None
            values: dict[str, Any] = dict(
                mask_asset_id=asset_id,
                object_label=object_label,
                geometry_json=geometry,
                origin="FIXTURE" if synthetic else "SAM",
                source_job_id=job_id,
                active=True,
                reviewed=False,
                reviewer=None,
                reviewed_at=None,
                revision_actor="segmentation-worker",
                revision_notes="Unapproved provider proposal.",
                revision_created_at=func.now(),
                updated_at=func.now(),
            )
            if old is None:
                self.db.execute(
                    self.t.seed_masks.insert().values(
                        seed_id=seed_id, object_ordinal=ordinal, **values
                    )
                )
            else:
                if not old["active"]:
                    self.archive(old)
                self.db.execute(
                    self.t.seed_masks.update()
                    .where(self.t.seed_masks.c.id == old["id"])
                    .values(revision=old["revision"] + 1, **values)
                )
        self.db.execute(
            self.t.seed_cases.update()
            .where(self.t.seed_cases.c.id == seed_id)
            .values(
                segmentation_json={
                    "job_id": str(job_id),
                    "detected_count": len(proposals),
                    "outcome": outcome,
                    "diagnostics": diagnostics,
                    "synthetic": synthetic,
                }
            )
        )
        self.touch(seed_id)
        self.s.audit(
            seed["project_id"],
            "SEGMENTATION_PROPOSED",
            "seed",
            seed_id,
            job_id=str(job_id),
            detected_count=len(proposals),
            outcome=outcome,
            synthetic=synthetic,
        )
        return True
