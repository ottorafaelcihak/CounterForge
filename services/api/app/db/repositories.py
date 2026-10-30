import hashlib
import json
from typing import Any, Literal, cast
from uuid import UUID

from counterforge_contracts.versions import ErrorCode
from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, RowMapping

from ..errors import ServiceError

JobKind = Literal[
    "SEGMENT", "TARGET_CHECK", "EXPORT", "RETEST", "CONFIRM", "SCENE", "MUTATION_SAM", "MINIMIZE"
]


class IdempotencyConflict(ValueError):
    """An existing project/kind/key describes a different request."""


class BackgroundJobs:
    """Durable job intent only; never dispatches work or commits a transaction.

    request_json/result_json must be sanitized application data. Credentials,
    headers, provider payloads and private prompts must not be passed here.
    """

    def __init__(
        self, connection: Connection, table: Table, experiments: Table | None = None
    ) -> None:
        self.connection = connection
        self.table = table
        self.experiments = experiments
        self.new_job_ids: list[UUID] = []
        self.max_pending_jobs = 100

    def get(self, job_id: UUID) -> RowMapping | None:
        return (
            self.connection.execute(select(self.table).where(self.table.c.id == job_id))
            .mappings()
            .one_or_none()
        )

    def enqueue(
        self,
        *,
        project_id: UUID,
        kind: JobKind,
        request_json: dict[str, Any],
        idempotency_key: str | None = None,
        experiment_id: UUID | None = None,
        finding_id: UUID | None = None,
    ) -> UUID:
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "request": request_json,
                    "experiment_id": str(experiment_id) if experiment_id else None,
                    "finding_id": str(finding_id) if finding_id else None,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        # One global queue bound; deduplication is checked before capacity.
        self.connection.execute(select(func.pg_advisory_xact_lock(73402031)))
        if idempotency_key is not None:
            existing = (
                self.connection.execute(
                    select(self.table).where(
                        self.table.c.project_id == project_id,
                        self.table.c.kind == kind,
                        self.table.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflict("Job key identifies a different request")
                return cast(UUID, existing["id"])
        pending = self.connection.scalar(
            select(func.count())
            .select_from(self.table)
            .where(
                self.table.c.state.in_(["QUEUED", "RUNNING"]),
                (self.table.c.experiment_id.is_(None) | (self.table.c.kind == "MINIMIZE")),
            )
        )
        if self.experiments is not None:
            pending = (pending or 0) + int(
                self.connection.scalar(
                    select(func.count())
                    .select_from(self.experiments)
                    .where(
                        self.experiments.c.dispatch_pending
                        | self.experiments.c.runner_token.is_not(None)
                    )
                )
                or 0
            )
        # Experiment-owned child stages share their admitted parent's slot.
        if (
            (experiment_id is None or kind == "MINIMIZE")
            and pending is not None
            and pending >= self.max_pending_jobs
        ):
            raise ServiceError(ErrorCode.QUEUE_FULL, "Setup queue is at capacity.", 429)
        statement = insert(self.table).values(
            project_id=project_id,
            kind=kind,
            request_json=request_json,
            request_fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            experiment_id=experiment_id,
            finding_id=finding_id,
        )
        job_id = self.connection.scalar(
            statement.on_conflict_do_nothing(
                index_elements=[
                    self.table.c.project_id,
                    self.table.c.kind,
                    self.table.c.idempotency_key,
                ]
            ).returning(self.table.c.id)
        )
        if job_id is not None:
            self.new_job_ids.append(job_id)
            return cast(UUID, job_id)
        existing = (
            self.connection.execute(
                select(self.table).where(
                    self.table.c.project_id == project_id,
                    self.table.c.kind == kind,
                    self.table.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one()
        )
        if existing["request_fingerprint"] != fingerprint:
            raise IdempotencyConflict("Job idempotency key already identifies a different request")
        return cast(UUID, existing["id"])
