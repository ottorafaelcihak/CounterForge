"""Atomic spend accounting shared by the API and the worker.

Every provider call must own a durable reservation before any network I/O:
``reserve_operation`` records an experiment stage intent, ``begin_operation``
claims its single dispatch, and ``settle_operation`` or ``mark_unknown_charge``
reconciles it. Pre-experiment work uses ``reserve_job_operation``, which requires
both explicit owner-authorized job limits and the shared deployment allowance.

Money is always a finite :class:`~decimal.Decimal` quantized to the database's
``numeric(12,6)``, rounded up for spend and down for ceilings; floats never take
part in the arithmetic. Conservative rules that must not be relaxed:

* an ambiguous provider outcome is consumed at its reserved upper bound, pauses
  the experiment and is never auto-refunded, auto-settled as free or voided;
* only an undispatched reservation can be voided;
* pending call and candidate quantities are derived from unfinished operation
  rows, while settled and unknown usage is already folded into the experiment
  counters, so no charge is counted twice;
* an unconfigured deployment has no paid allowance at all.

This module never dispatches work, never opens a provider connection and never
commits: the caller owns the :class:`UnitOfWork` transaction boundary.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Final, cast
from uuid import UUID

from counterforge_contracts.requests import JobLimits
from counterforge_contracts.versions import ErrorCode
from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping

from .db.uow import UnitOfWork
from .errors import ServiceError

CENT: Final = Decimal("0.000001")
ZERO: Final = Decimal("0.000000")
MAX_MONEY: Final = Decimal("999999.999999")
MAX_QUANTITY: Final = 1000
MAX_COUNTER: Final = 10**9
MAX_ALLOWANCE_WINDOW: Final = timedelta(days=30)
# States in which a runner may still create new provider intent.
ACTIVE_STATES: Final = frozenset(
    {
        "BASELINE_VALIDATING",
        "READY",
        "PLANNING",
        "MUTATING",
        "VALIDATING",
        "EVALUATING",
        "CONFIRMING",
    }
)
PENDING_OPERATIONS: Final = frozenset({"RESERVED", "RUNNING"})
RUNNABLE_JOB_STATES: Final = frozenset({"QUEUED", "RUNNING"})
USAGE_CATEGORIES: Final = frozenset({"PLANNER", "IMAGE", "SAM", "TARGET", "OTHER"})


def money(value: Decimal | int, *, label: str = "amount", ceiling: bool = True) -> Decimal:
    """Return ``value`` as a finite ``numeric(12,6)`` Decimal.

    Spend and reservations round up, declared ceilings round down, so rounding
    can never invent budget. Floats are rejected rather than silently converted.
    """

    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise ServiceError(
            ErrorCode.INVALID_INPUT, f"{label} must be a finite Decimal amount.", 422
        )
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0 or amount > MAX_MONEY:
        raise ServiceError(
            ErrorCode.INVALID_INPUT, f"{label} must be a finite, non-negative amount.", 422
        )
    return amount.quantize(CENT, rounding=ROUND_CEILING if ceiling else ROUND_FLOOR)


def quantity(value: int, *, label: str, minimum: int = 0) -> int:
    """Return a bounded non-negative count; provider quantities are never floats."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ServiceError(ErrorCode.INVALID_INPUT, f"{label} must be a whole number.", 422)
    if not minimum <= value <= MAX_QUANTITY:
        raise ServiceError(
            ErrorCode.INVALID_INPUT, f"{label} must be between {minimum} and {MAX_QUANTITY}.", 422
        )
    return value


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Reconciled usage reported by a provider response. Cost must be Decimal."""

    actual_usd: Decimal
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class OperationReservation:
    operation_key: str
    stage: str
    created: bool
    upper_bound_usd: Decimal
    target_calls: int
    candidate_slots: int
    experiment_id: UUID | None = None
    background_job_id: UUID | None = None
    operation_id: UUID | None = None
    usage_event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class UsageSettlement:
    operation_key: str
    actual_usd: Decimal
    released_usd: Decimal
    calls_consumed: int
    candidates_created: int
    recorded: bool
    above_reservation: bool


@dataclass(frozen=True, slots=True)
class UnknownCharge:
    operation_key: str
    consumed_usd: Decimal
    reason: str
    recorded: bool


@dataclass(frozen=True, slots=True)
class VoidedOperation:
    operation_key: str
    released_usd: Decimal
    recorded: bool


@dataclass(frozen=True, slots=True)
class DeploymentAllowance:
    max_usd: Decimal
    max_calls: int
    consumed_usd: Decimal
    consumed_calls: int
    expires_at: datetime
    configured_by: str


class Budget:
    """Spend control bound to one transaction; the caller commits or rolls back.

    Locks are always taken parent-first (experiment or background job, then the
    operation and usage rows, then the shared allowance) so competing reservations
    serialize without deadlocking.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow
        self.db = uow.connection
        self.t = uow.tables

    # ----- shared helpers -------------------------------------------------

    def _now(self) -> datetime:
        """Statement clock: a long transaction must not extend a runtime cap."""

        return cast(datetime, self.db.scalar(select(func.statement_timestamp())))

    def _lock_key(self, key: str) -> None:
        # Serialize first creation across different parent rows/scopes, too.
        self.db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended("counterforge:operation:" + key, 0)
                )
            )
        )

    def _usage_metadata(self, value: ProviderUsage) -> dict[str, Any]:
        request_id = value.provider_request_id
        if request_id is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", request_id):
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Provider request ID must be a bounded identifier.", 422
            )
        return {
            "tokens_in": self._counter(value.tokens_in, label="tokens_in"),
            "tokens_out": self._counter(value.tokens_out, label="tokens_out"),
            "latency_ms": self._counter(value.latency_ms, label="latency_ms"),
            "provider_request_id": request_id,
        }

    def _same_settlement(self, usage: RowMapping, actual: Decimal, value: ProviderUsage) -> bool:
        return money(usage["actual_usd"] or 0) == actual and all(
            usage[name] == item for name, item in self._usage_metadata(value).items()
        )

    @staticmethod
    def _key(operation_key: str) -> str:
        key = operation_key.strip()
        if not 1 <= len(key) <= 200:
            raise ServiceError(ErrorCode.INVALID_INPUT, "An operation key is required.", 422)
        return key

    @staticmethod
    def _category(stage: str) -> str:
        return stage if stage in USAGE_CATEGORIES else "OTHER"

    @staticmethod
    def _counter(value: int | None, *, label: str) -> int | None:
        """Optional provider counters: non-negative whole numbers, never floats."""

        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNTER:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, f"{label} must be a non-negative count.", 422
            )
        return value

    def _experiment(self, experiment_id: UUID) -> RowMapping:
        row = (
            self.db.execute(
                select(self.t.experiments)
                .where(self.t.experiments.c.id == experiment_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Experiment was not found.", 404)
        return row

    def _operation(self, operation_key: str, *, lock: bool) -> RowMapping | None:
        query = select(self.t.run_operations).where(
            self.t.run_operations.c.operation_key == operation_key
        )
        if lock:
            query = query.with_for_update()
        return self.db.execute(query).mappings().one_or_none()

    def _usage(self, operation_key: str, *, lock: bool) -> RowMapping | None:
        query = select(self.t.usage_events).where(
            self.t.usage_events.c.operation_key == operation_key
        )
        if lock:
            query = query.with_for_update()
        return self.db.execute(query).mappings().one_or_none()

    def _locked_pair(self, operation_key: str) -> tuple[RowMapping, RowMapping]:
        operation = self._operation(operation_key, lock=True)
        usage = self._usage(operation_key, lock=True)
        if operation is None or usage is None:
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Operation intent and recorded usage must both exist."
            )
        return operation, usage

    def _require_runnable(self, experiment: RowMapping) -> None:
        from .evaluation import require_runtime

        require_runtime(self, experiment)
        if experiment["state"] not in ACTIVE_STATES:
            raise ServiceError(
                ErrorCode.INVALID_STATE,
                f"Experiment state {experiment['state']} does not allow provider work.",
            )
        started = experiment["started_at"]
        if started is None:
            raise ServiceError(ErrorCode.INVALID_STATE, "Experiment has not started.")
        if (self._now() - started).total_seconds() > experiment["max_runtime_seconds"]:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Experiment exceeded its maximum runtime."
            )

    def require_refinement_source(self, experiment: Any) -> None:
        """Completed source only; original wall clock and every ceiling still bind."""
        if (
            experiment["state"] != "COMPLETED"
            or experiment["runner_token"] is not None
            or experiment["dispatch_pending"]
        ):
            raise ServiceError(
                ErrorCode.INVALID_STATE,
                "Refinement requires a completed source with no active runner.",
            )
        if (
            experiment["started_at"] is None
            or (self._now() - experiment["started_at"]).total_seconds()
            >= experiment["max_runtime_seconds"]
        ):
            raise ServiceError(ErrorCode.BUDGET_EXCEEDED, "Source experiment runtime has expired.")
        if self.db.scalar(
            select(self.t.usage_events.c.id)
            .where(
                self.t.usage_events.c.experiment_id == experiment["id"],
                self.t.usage_events.c.status.in_(["RESERVED", "UNKNOWN_CHARGE"]),
            )
            .limit(1)
        ):
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Source has unresolved provider work.")

    def require_followup(self, experiment: Any, job_id: UUID) -> Any:
        """Server-owned MINIMIZE intent and current claim authorize the narrow exception."""
        j = self.t.background_jobs
        job = self.db.execute(select(j).where(j.c.id == job_id).with_for_update()).mappings().one()
        f = (
            self.db.execute(
                select(self.t.findings).where(self.t.findings.c.id == job["finding_id"])
            )
            .mappings()
            .one()
        )
        if (
            job["kind"] != "MINIMIZE"
            or job["experiment_id"] != experiment["id"]
            or job["project_id"] != experiment["project_id"]
            or f["experiment_id"] != experiment["id"]
            or job["request_json"].get("source_mutation_id") != str(f["mutation_id"])
            or job["state"] != "RUNNING"
            or job["claim_token"] is None
            or job["lease_expires_at"] is None
            or job["lease_expires_at"] <= self._now()
        ):
            raise ServiceError(ErrorCode.INVALID_STATE, "Refinement context is no longer owned.")
        # Reserved operations belonging to this exact job are permitted while its
        # fenced runner is active. All other pending/unknown work stops dispatch.
        if (
            experiment["state"] != "COMPLETED"
            or experiment["runner_token"] is not None
            or experiment["dispatch_pending"]
        ):
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Source is no longer available for refinement."
            )
        if (
            experiment["started_at"] is None
            or (self._now() - experiment["started_at"]).total_seconds()
            >= experiment["max_runtime_seconds"]
        ):
            raise ServiceError(ErrorCode.BUDGET_EXCEEDED, "Source experiment runtime has expired.")
        u = self.t.usage_events
        if self.db.scalar(
            select(u.c.id)
            .where(
                u.c.experiment_id == experiment["id"],
                (u.c.status == "UNKNOWN_CHARGE")
                | ((u.c.status == "RESERVED") & u.c.background_job_id.is_distinct_from(job_id)),
            )
            .limit(1)
        ):
            raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Source has unresolved provider work.")
        return job

    def reserve_followup_operation(
        self,
        experiment_id: UUID,
        job_id: UUID,
        operation_key: str,
        stage: str,
        upper_bound_usd: Decimal,
        **kwargs: Any,
    ) -> OperationReservation:
        return self.reserve_operation(
            experiment_id, operation_key, stage, upper_bound_usd, _followup_job_id=job_id, **kwargs
        )

    def _pending(self, experiment_id: UUID) -> tuple[int, int]:
        """Reserved calls and candidate slots derived from unfinished operations."""

        operations = self.t.run_operations
        row = self.db.execute(
            select(
                func.coalesce(func.sum(operations.c.target_calls), 0),
                func.coalesce(func.sum(operations.c.candidate_slots), 0),
            ).where(
                operations.c.experiment_id == experiment_id,
                operations.c.status.in_(sorted(PENDING_OPERATIONS)),
            )
        ).one()
        return int(row[0]), int(row[1])

    def _event(self, experiment_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            self.t.experiment_events.insert().values(
                experiment_id=experiment_id,
                event_type=event_type,
                payload_json=payload,
            )
        )

    def _audit(
        self,
        *,
        action: str,
        actor: str,
        entity_type: str,
        entity_id: str,
        project_id: UUID | None = None,
        **details: Any,
    ) -> None:
        self.db.execute(
            self.t.audit_events.insert().values(
                project_id=project_id,
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details_json=details,
            )
        )

    # ----- experiment reservations ---------------------------------------

    def reserve_operation(
        self,
        experiment_id: UUID,
        operation_key: str,
        stage: str,
        upper_bound_usd: Decimal,
        target_calls: int = 0,
        candidate_slots: int = 0,
        *,
        provider: str | None = None,
        model_id: str | None = None,
        semantic_sha256: str | None = None,
        _followup_job_id: UUID | None = None,
    ) -> OperationReservation:
        """Record durable intent for one experiment stage before any dispatch.

        The experiment row is locked first, so two competing reservations cannot
        both pass the dollar, call or candidate ceiling. Replaying the same
        operation key with the same request returns ``created=False`` instead of
        reserving twice; a changed request is an idempotency conflict.
        """

        key = self._key(operation_key)
        if semantic_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", semantic_sha256):
            raise ServiceError(ErrorCode.INVALID_INPUT, "Invalid semantic fingerprint.", 422)
        upper = money(upper_bound_usd, label="upper_bound_usd")
        calls = quantity(target_calls, label="target_calls")
        slots = quantity(candidate_slots, label="candidate_slots")
        digest = _fingerprint(
            {
                "experiment_id": str(experiment_id),
                "stage": stage,
                "upper_bound_usd": str(upper),
                "target_calls": calls,
                "candidate_slots": slots,
                "provider": provider,
                "model_id": model_id,
                "semantic_sha256": semantic_sha256,
                **({"followup_job_id": str(_followup_job_id)} if _followup_job_id else {}),
            }
        )
        self._lock_key(key)
        experiment = self._experiment(experiment_id)
        existing = self._operation(key, lock=True)
        if existing is not None:
            if (
                existing["request_fingerprint"] != digest
                or existing["experiment_id"] != experiment_id
            ):
                raise ServiceError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Operation key already describes a different request.",
                )
            usage = self._usage(key, lock=True)
            return OperationReservation(
                operation_key=key,
                stage=existing["stage"],
                created=False,
                upper_bound_usd=money(usage["estimated_max_usd"]) if usage else upper,
                target_calls=existing["target_calls"],
                candidate_slots=existing["candidate_slots"],
                experiment_id=experiment_id,
                operation_id=existing["id"],
                usage_event_id=usage["id"] if usage else None,
            )
        if self._usage(key, lock=True) is not None:
            raise ServiceError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Operation key already identifies other recorded usage.",
            )
        if _followup_job_id is None:
            self._require_runnable(experiment)
        else:
            self.require_followup(experiment, _followup_job_id)
        pending_calls, pending_slots = self._pending(experiment_id)
        if experiment["calls_consumed"] + pending_calls + calls > experiment["max_target_calls"]:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Reservation would exceed the target-call cap."
            )
        if experiment["candidates_created"] + pending_slots + slots > experiment["max_candidates"]:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Reservation would exceed the candidate cap."
            )
        committed = money(experiment["spent_usd"]) + money(experiment["reserved_usd"])
        if committed + upper > money(experiment["max_estimated_usd"], ceiling=False):
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Reservation would exceed the estimated spend cap."
            )
        from .evaluation import reserve_global

        reserve_global(self, experiment, upper, max(calls, 1))
        category = self._category(stage)
        pinned = cast(dict[str, Any], experiment["config_snapshot"] or {}).get("target") or {}
        operation_id = self.db.scalar(
            self.t.run_operations.insert()
            .values(
                experiment_id=experiment_id,
                operation_key=key,
                stage=stage,
                status="RESERVED",
                target_calls=calls,
                candidate_slots=slots,
                request_fingerprint=digest,
            )
            .returning(self.t.run_operations.c.id)
        )
        usage_id = self.db.scalar(
            self.t.usage_events.insert()
            .values(
                experiment_id=experiment_id,
                background_job_id=_followup_job_id,
                operation_key=key,
                provider=provider or self._pinned(pinned, "provider", category),
                model_id=model_id or self._pinned(pinned, "model_id", category),
                category=category,
                status="RESERVED",
                estimated_max_usd=upper,
                reserved_calls=calls,
                request_count=max(calls, 1),
                semantic_sha256=semantic_sha256,
                request_fingerprint=digest,
            )
            .returning(self.t.usage_events.c.id)
        )
        self.db.execute(
            self.t.experiments.update()
            .where(self.t.experiments.c.id == experiment_id)
            .values(reserved_usd=self.t.experiments.c.reserved_usd + upper)
        )
        return OperationReservation(
            operation_key=key,
            stage=stage,
            created=True,
            upper_bound_usd=upper,
            target_calls=calls,
            candidate_slots=slots,
            experiment_id=experiment_id,
            operation_id=cast(UUID, operation_id),
            usage_event_id=cast(UUID, usage_id),
        )

    @staticmethod
    def _pinned(pinned: dict[str, Any], field: str, category: str) -> str:
        """Target usage is attributed to the frozen snapshot; other stages stay explicit."""

        value = pinned.get(field) if category == "TARGET" else None
        return str(value) if value else "UNCONFIGURED"

    def begin_operation(self, operation_key: str) -> bool:
        """Claim the single dispatch for a reservation.

        Returns ``True`` for the one caller that may now perform the provider
        call and ``False`` for every repeat, including after settlement. State
        and runtime are re-checked here, because a reservation may have been made
        before the experiment stopped or its runtime expired.
        """

        key = self._key(operation_key)
        probe = self._operation(key, lock=False)
        if probe is None:
            return self._begin_job_operation(key)
        experiment = self._experiment(probe["experiment_id"])
        operation = self._operation(key, lock=True)
        if operation is None or operation["status"] != "RESERVED":
            return False
        usage = self._usage(key, lock=True)
        if usage is not None and usage["background_job_id"] is not None:
            job = self._job_for_usage(usage)
            if job["kind"] == "MINIMIZE":
                self.require_followup(experiment, job["id"])
            else:
                self._require_runnable(experiment)
        else:
            self._require_runnable(experiment)
        claimed = self.db.execute(
            self.t.run_operations.update()
            .where(
                self.t.run_operations.c.operation_key == key,
                self.t.run_operations.c.status == "RESERVED",
            )
            .values(status="RUNNING", started_at=func.now())
        ).rowcount
        if claimed != 1:
            return False
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(dispatched_at=func.now())
        )
        return True

    def settle_operation(
        self, operation_key: str, provider_usage: ProviderUsage
    ) -> UsageSettlement:
        """Record reconciled cost and release the surplus reservation.

        An identical repeat is idempotent; a changed settlement is a conflict.
        Cost above the reservation is still recorded in full and stops further
        work, because the money was already spent.
        """

        key = self._key(operation_key)
        actual = money(provider_usage.actual_usd, label="actual_usd")
        self._usage_metadata(provider_usage)
        probe = self._operation(key, lock=False)
        if probe is None:
            return self._settle_job_usage(key, actual, provider_usage)
        experiment = self._experiment(probe["experiment_id"])
        operation, usage = self._locked_pair(key)
        if usage["status"] == "SETTLED" and usage["cost_kind"] not in (None, "ACTUAL"):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Non-actual billing requires explicit reconciliation."
            )
        if usage["status"] == "SETTLED" or operation["status"] == "DONE":
            if usage["status"] != "SETTLED" or not self._same_settlement(
                usage, actual, provider_usage
            ):
                raise ServiceError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Settled provider usage cannot change."
                )
            return UsageSettlement(
                operation_key=key,
                actual_usd=actual,
                released_usd=ZERO,
                calls_consumed=operation["target_calls"],
                candidates_created=operation["candidate_slots"],
                recorded=False,
                above_reservation=actual > money(usage["estimated_max_usd"]),
            )
        if usage["status"] == "UNKNOWN_CHARGE" or operation["status"] == "UNKNOWN":
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "An unresolved provider charge must be resolved by an operator.",
            )
        if usage["status"] == "VOID" or operation["status"] == "FAILED":
            raise ServiceError(ErrorCode.INVALID_STATE, "A voided operation cannot be settled.")
        if operation["status"] != "RUNNING":
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Only a dispatched operation can be settled."
            )
        reserved = money(usage["estimated_max_usd"])
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(
                status="SETTLED",
                cost_kind="ACTUAL",
                actual_usd=actual,
                settled_at=func.now(),
                tokens_in=self._counter(provider_usage.tokens_in, label="tokens_in"),
                tokens_out=self._counter(provider_usage.tokens_out, label="tokens_out"),
                latency_ms=self._counter(provider_usage.latency_ms, label="latency_ms"),
                provider_request_id=provider_usage.provider_request_id,
            )
        )
        self.db.execute(
            self.t.run_operations.update()
            .where(self.t.run_operations.c.operation_key == key)
            .values(
                status="DONE",
                finished_at=func.now(),
                result_json={"actual_usd": str(actual), "estimated_max_usd": str(reserved)},
            )
        )
        experiments = self.t.experiments
        values: dict[str, Any] = {
            "reserved_usd": func.greatest(experiments.c.reserved_usd - reserved, ZERO),
            "spent_usd": experiments.c.spent_usd + actual,
            "calls_consumed": experiments.c.calls_consumed + operation["target_calls"],
            "candidates_created": experiments.c.candidates_created + operation["candidate_slots"],
        }
        spent = money(experiment["spent_usd"]) + actual
        remaining_reserved = max(money(experiment["reserved_usd"]) - reserved, ZERO)
        above = actual > reserved
        if experiment["state"] in ACTIVE_STATES or experiment["state"] == "PAUSE_REQUESTED":
            if spent + remaining_reserved > money(experiment["max_estimated_usd"], ceiling=False):
                values |= {
                    "state": "BUDGET_EXHAUSTED",
                    "stop_reason": "ESTIMATED_SPEND_CAP_REACHED",
                    "ended_at": func.now(),
                }
            elif above:
                # The charge is real; stop and let an operator review pricing.
                values |= {"state": "PAUSED", "stop_reason": "PROVIDER_COST_ABOVE_RESERVATION"}
        self.db.execute(
            experiments.update().where(experiments.c.id == experiment["id"]).values(**values)
        )
        if "state" in values:
            self._event(
                experiment["id"],
                "EXPERIMENT_STOPPED",
                {
                    "state": values["state"],
                    "reason": values["stop_reason"],
                    "operation_key": key,
                },
            )
        return UsageSettlement(
            operation_key=key,
            actual_usd=actual,
            released_usd=max(reserved - actual, ZERO),
            calls_consumed=operation["target_calls"],
            candidates_created=operation["candidate_slots"],
            recorded=True,
            above_reservation=above,
        )

    def mark_unknown_charge(self, operation_key: str, reason: str) -> UnknownCharge:
        """Consume an ambiguous outcome once at its reserved upper bound.

        A request that may already have been billed is never refunded and never
        settled as free. The experiment is paused so no scheduler blindly repeats
        a possibly charged call.
        """

        key = self._key(operation_key)
        raw_reason = reason.strip()
        note = "PROVIDER_OUTCOME_UNKNOWN"
        if not 1 <= len(raw_reason) <= 500:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "An unresolved charge requires a short reason.", 422
            )
        probe = self._operation(key, lock=False)
        if probe is None:
            return self._unknown_job_usage(key, note)
        experiment = self._experiment(probe["experiment_id"])
        operation, usage = self._locked_pair(key)
        if usage["status"] == "UNKNOWN_CHARGE" or operation["status"] == "UNKNOWN":
            return UnknownCharge(
                operation_key=key,
                consumed_usd=money(usage["estimated_max_usd"]),
                reason=note,
                recorded=False,
            )
        if usage["status"] == "SETTLED":
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Settled usage cannot become an unresolved charge."
            )
        if usage["status"] == "VOID":
            raise ServiceError(ErrorCode.INVALID_STATE, "A voided operation records no charge.")
        if operation["status"] != "RUNNING" or usage["dispatched_at"] is None:
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Only dispatched intent can have an unresolved charge."
            )
        consumed = money(usage["estimated_max_usd"])
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(
                status="UNKNOWN_CHARGE",
                cost_kind="UNKNOWN",
                dispatched_at=func.coalesce(self.t.usage_events.c.dispatched_at, func.now()),
            )
        )
        self.db.execute(
            self.t.run_operations.update()
            .where(self.t.run_operations.c.operation_key == key)
            .values(
                status="UNKNOWN",
                finished_at=func.now(),
                error_code="PROVIDER_OUTCOME_UNKNOWN",
                result_json={"reason": note, "consumed_usd": str(consumed)},
            )
        )
        experiments = self.t.experiments
        values: dict[str, Any] = {
            "reserved_usd": func.greatest(experiments.c.reserved_usd - consumed, ZERO),
            "spent_usd": experiments.c.spent_usd + consumed,
            "calls_consumed": experiments.c.calls_consumed + operation["target_calls"],
            "candidates_created": experiments.c.candidates_created + operation["candidate_slots"],
        }
        if experiment["state"] in ACTIVE_STATES or experiment["state"] == "PAUSE_REQUESTED":
            values |= {"state": "PAUSED", "stop_reason": "UNRESOLVED_PROVIDER_CHARGE"}
        self.db.execute(
            experiments.update().where(experiments.c.id == experiment["id"]).values(**values)
        )
        if values.get("state") == "PAUSED":
            self._event(
                experiment["id"],
                "EXPERIMENT_STATE_CHANGED",
                {"state": "PAUSED", "reason": "UNRESOLVED_PROVIDER_CHARGE"},
            )
        self._event(
            experiment["id"],
            "UNKNOWN_CHARGE_RECORDED",
            {"operation_key": key, "reason": note, "consumed_usd": str(consumed)},
        )
        return UnknownCharge(operation_key=key, consumed_usd=consumed, reason=note, recorded=True)

    def void_operation(self, operation_key: str) -> VoidedOperation:
        """Release an intent that was never dispatched; anything else must not be voided."""

        key = self._key(operation_key)
        probe = self._operation(key, lock=False)
        if probe is None:
            return self._void_job_operation(key)
        experiment = self._experiment(probe["experiment_id"])
        operation, usage = self._locked_pair(key)
        if usage["status"] == "VOID":
            return VoidedOperation(operation_key=key, released_usd=ZERO, recorded=False)
        if usage["status"] != "RESERVED" or operation["status"] != "RESERVED":
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Only an undispatched reservation can be voided."
            )
        released = money(usage["estimated_max_usd"])
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(status="VOID", cost_kind="UNBILLED", actual_usd=ZERO)
        )
        self.db.execute(
            self.t.run_operations.update()
            .where(self.t.run_operations.c.operation_key == key)
            .values(status="FAILED", finished_at=func.now(), error_code="OPERATION_VOIDED")
        )
        experiments = self.t.experiments
        self.db.execute(
            experiments.update()
            .where(experiments.c.id == experiment["id"])
            .values(reserved_usd=func.greatest(experiments.c.reserved_usd - released, ZERO))
        )
        return VoidedOperation(operation_key=key, released_usd=released, recorded=True)

    # ----- pre-experiment jobs -------------------------------------------

    def configure_deployment_allowance(
        self,
        *,
        max_usd: Decimal,
        max_calls: int,
        expires_at: datetime,
        configured_by: str = "operator",
    ) -> DeploymentAllowance:
        """Operator-only control for the shared pre-experiment allowance.

        There is no default paid allowance and this is never reachable from the
        public API. Updating the limits deliberately leaves the consumed totals
        untouched, so raising a limit can never erase recorded spend.
        """

        limit = money(max_usd, label="max_usd", ceiling=False)
        calls = quantity(max_calls, label="max_calls")
        actor = configured_by.strip()
        if not 1 <= len(actor) <= 100:
            raise ServiceError(ErrorCode.INVALID_INPUT, "An operator label is required.", 422)
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ServiceError(
                ErrorCode.INVALID_INPUT, "Allowance expiry must be timezone-aware.", 422
            )
        now = self._now()
        if not now < expires_at <= now + MAX_ALLOWANCE_WINDOW:
            raise ServiceError(
                ErrorCode.INVALID_INPUT,
                "Allowance expiry must be in the future and at most 30 days ahead.",
                422,
            )
        table = self.t.deployment_allowance
        statement = pg_insert(table).values(
            singleton=True,
            max_usd=limit,
            max_calls=calls,
            expires_at=expires_at,
            configured_by=actor,
        )
        row = (
            self.db.execute(
                statement.on_conflict_do_update(
                    index_elements=[table.c.singleton],
                    set_={
                        "max_usd": limit,
                        "max_calls": calls,
                        "expires_at": expires_at,
                        "configured_by": actor,
                        "updated_at": func.now(),
                    },
                ).returning(table)
            )
            .mappings()
            .one()
        )
        self._audit(
            action="DEPLOYMENT_ALLOWANCE_CONFIGURED",
            actor=actor,
            entity_type="deployment_allowance",
            entity_id="singleton",
            max_usd=str(limit),
            max_calls=calls,
            expires_at=expires_at.isoformat(),
            consumed_usd=str(money(row["consumed_usd"])),
            consumed_calls=row["consumed_calls"],
        )
        return self._allowance_dto(row)

    @staticmethod
    def _allowance_dto(row: RowMapping) -> DeploymentAllowance:
        return DeploymentAllowance(
            max_usd=money(row["max_usd"], ceiling=False),
            max_calls=row["max_calls"],
            consumed_usd=money(row["consumed_usd"]),
            consumed_calls=row["consumed_calls"],
            expires_at=row["expires_at"],
            configured_by=row["configured_by"],
        )

    def deployment_allowance(self) -> DeploymentAllowance | None:
        """Report the configured allowance, if an operator authorized one."""

        row = self.db.execute(select(self.t.deployment_allowance)).mappings().one_or_none()
        return None if row is None else self._allowance_dto(row)

    def reserve_job_operation(
        self,
        background_job_id: UUID,
        operation_key: str,
        stage: str,
        upper_bound_usd: Decimal,
        calls: int = 1,
        *,
        provider: str | None = None,
        model_id: str | None = None,
        semantic_sha256: str | None = None,
    ) -> OperationReservation:
        """Authorize one pre-experiment provider call against two finite limits.

        A segmentation or smoke call is never an unaccounted exception: the job
        must carry explicit owner-authorized limits in its sanitized request, and
        every provider call it makes also consumes the shared deployment
        allowance. Usage is recorded with a null experiment and the job's id;
        ``run_operations`` stays experiment-only.
        """

        key = self._key(operation_key)
        if semantic_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", semantic_sha256):
            raise ServiceError(ErrorCode.INVALID_INPUT, "Invalid semantic fingerprint.", 422)
        upper = money(upper_bound_usd, label="upper_bound_usd")
        count = quantity(calls, label="calls", minimum=1)
        digest = _fingerprint(
            {
                "background_job_id": str(background_job_id),
                "stage": stage,
                "upper_bound_usd": str(upper),
                "calls": count,
                "provider": provider,
                "model_id": model_id,
                "semantic_sha256": semantic_sha256,
            }
        )
        self._lock_key(key)
        jobs = self.t.background_jobs
        job = (
            self.db.execute(select(jobs).where(jobs.c.id == background_job_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if job is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Background job was not found.", 404)
        if job["experiment_id"] is not None:
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Experiment work must use experiment reservations."
            )
        existing = self._usage(key, lock=True)
        if existing is not None:
            if (
                existing["background_job_id"] != background_job_id
                or existing["request_fingerprint"] != digest
            ):
                raise ServiceError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Operation key already describes a different request.",
                )
            return OperationReservation(
                operation_key=key,
                stage=stage,
                created=False,
                upper_bound_usd=money(existing["estimated_max_usd"]),
                target_calls=existing["reserved_calls"],
                candidate_slots=0,
                background_job_id=background_job_id,
                usage_event_id=existing["id"],
            )
        if self._operation(key, lock=True) is not None:
            raise ServiceError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Operation key already identifies an experiment operation.",
            )
        if job["state"] not in RUNNABLE_JOB_STATES:
            raise ServiceError(
                ErrorCode.INVALID_STATE,
                f"Job state {job['state']} does not allow provider work.",
            )
        limits = self._job_limits(job)
        started = job["started_at"] or job["created_at"]
        if (self._now() - started).total_seconds() > limits.max_runtime_seconds:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Job exceeded its authorized runtime limit."
            )
        # The authorized limit arrives as a JSON number; str() keeps the conversion exact.
        ceiling = money(
            Decimal(str(limits.max_estimated_usd)), label="max_estimated_usd", ceiling=False
        )
        used_usd, used_calls = self._job_usage(background_job_id)
        if used_usd + upper > ceiling:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Reservation would exceed the authorized job spend."
            )
        if used_calls + count > limits.max_calls:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Reservation would exceed the authorized job calls."
            )
        self._consume_allowance(upper, count)
        usage_id = self.db.scalar(
            self.t.usage_events.insert()
            .values(
                experiment_id=None,
                background_job_id=background_job_id,
                operation_key=key,
                provider=provider or "UNCONFIGURED",
                model_id=model_id or "UNCONFIGURED",
                category=self._category(stage),
                status="RESERVED",
                estimated_max_usd=upper,
                reserved_calls=count,
                request_count=count,
                semantic_sha256=semantic_sha256,
                request_fingerprint=digest,
            )
            .returning(self.t.usage_events.c.id)
        )
        self._audit(
            action="JOB_OPERATION_RESERVED",
            actor="worker",
            entity_type="background_job",
            entity_id=str(background_job_id),
            project_id=job["project_id"],
            operation_key=key,
            stage=stage,
            upper_bound_usd=str(upper),
            calls=count,
        )
        return OperationReservation(
            operation_key=key,
            stage=stage,
            created=True,
            upper_bound_usd=upper,
            target_calls=count,
            candidate_slots=0,
            background_job_id=background_job_id,
            usage_event_id=cast(UUID, usage_id),
        )

    @staticmethod
    def _job_limits(job: RowMapping) -> JobLimits:
        request = job["request_json"]
        raw = request.get("limits") if isinstance(request, dict) else None
        if not isinstance(raw, dict):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "This job has no explicit owner-authorized limits; dispatch requires them.",
            )
        try:
            return JobLimits.model_validate(raw)
        except ValidationError as error:
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "This job's authorized limits are not valid."
            ) from error

    def _job_usage(self, background_job_id: UUID) -> tuple[Decimal, int]:
        """Consumed dollars and calls for one job, counting every provider call."""

        usage = self.t.usage_events
        row = self.db.execute(
            select(
                func.coalesce(
                    func.sum(func.coalesce(usage.c.actual_usd, usage.c.estimated_max_usd)), 0
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (usage.c.cost_kind == "UNBILLED")
                                & (
                                    usage.c.metadata_json["nonactual_proof"]["request_sent"]
                                    .as_boolean()
                                    .is_(False)
                                ),
                                0,
                            ),
                            else_=usage.c.reserved_calls,
                        )
                    ),
                    0,
                ),
            ).where(
                usage.c.background_job_id == background_job_id,
                usage.c.status != "VOID",
            )
        ).one()
        return money(row[0]), int(row[1])

    def _consume_allowance(self, upper: Decimal, calls: int) -> DeploymentAllowance:
        """Lock the singleton allowance and consume it conservatively, up front."""

        table = self.t.deployment_allowance
        row = self.db.execute(select(table).with_for_update()).mappings().one_or_none()
        if row is None:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED,
                "No deployment allowance is configured; an operator must authorize paid work.",
            )
        if row["expires_at"] <= self._now():
            raise ServiceError(ErrorCode.BUDGET_EXCEEDED, "The deployment allowance has expired.")
        consumed_usd = money(row["consumed_usd"])
        consumed_calls = int(row["consumed_calls"])
        if (
            consumed_usd + upper > money(row["max_usd"], ceiling=False)
            or consumed_calls + calls > row["max_calls"]
        ):
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED,
                "Reservation would exceed the shared deployment allowance.",
            )
        self.db.execute(
            table.update().values(
                consumed_usd=table.c.consumed_usd + upper,
                consumed_calls=table.c.consumed_calls + calls,
                updated_at=func.now(),
            )
        )
        return DeploymentAllowance(
            max_usd=money(row["max_usd"], ceiling=False),
            max_calls=row["max_calls"],
            consumed_usd=consumed_usd + upper,
            consumed_calls=consumed_calls + calls,
            expires_at=row["expires_at"],
            configured_by=row["configured_by"],
        )

    def _job_for_usage(self, usage: RowMapping) -> RowMapping:
        jobs = self.t.background_jobs
        row = (
            self.db.execute(
                select(jobs).where(jobs.c.id == usage["background_job_id"]).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Background job was not found.", 404)
        return row

    def _begin_job_operation(self, key: str) -> bool:
        probe = self._usage(key, lock=False)
        if probe is None or probe["background_job_id"] is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        job = self._job_for_usage(probe)
        usage = self._usage(key, lock=True)
        if usage is None or usage["status"] != "RESERVED" or usage["dispatched_at"] is not None:
            return False
        if job["experiment_id"] is not None or job["state"] not in RUNNABLE_JOB_STATES:
            raise ServiceError(ErrorCode.INVALID_STATE, "Job is not runnable.")
        limits = self._job_limits(job)
        if (
            self._now() - (job["started_at"] or job["created_at"])
        ).total_seconds() > limits.max_runtime_seconds:
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Job exceeded its authorized runtime limit."
            )
        allowance = (
            self.db.execute(select(self.t.deployment_allowance).with_for_update())
            .mappings()
            .one_or_none()
        )
        if (
            allowance is None
            or allowance["expires_at"] <= self._now()
            or allowance["consumed_usd"] > allowance["max_usd"]
            or allowance["consumed_calls"] > allowance["max_calls"]
        ):
            raise ServiceError(
                ErrorCode.BUDGET_EXCEEDED, "Deployment allowance is unavailable or exhausted."
            )
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(dispatched_at=func.now())
        )
        jobs = self.t.background_jobs
        self.db.execute(
            jobs.update()
            .where(jobs.c.id == job["id"])
            .values(
                state="RUNNING",
                started_at=func.coalesce(jobs.c.started_at, func.now()),
                heartbeat_at=func.now(),
                updated_at=func.now(),
            )
        )
        return True

    def _void_job_operation(self, key: str) -> VoidedOperation:
        probe = self._usage(key, lock=False)
        if probe is None or probe["background_job_id"] is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        self._job_for_usage(probe)
        usage = self._usage(key, lock=True)
        if usage is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        if usage["status"] == "VOID":
            return VoidedOperation(operation_key=key, released_usd=ZERO, recorded=False)
        if usage["status"] != "RESERVED" or usage["dispatched_at"] is not None:
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Only undispatched job intent can be voided."
            )
        released = money(usage["estimated_max_usd"])
        allowance = self.t.deployment_allowance
        self.db.execute(select(allowance).with_for_update()).one()
        self.db.execute(
            allowance.update().values(
                consumed_usd=allowance.c.consumed_usd - released,
                consumed_calls=allowance.c.consumed_calls - usage["reserved_calls"],
                updated_at=func.now(),
            )
        )
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(status="VOID", cost_kind="UNBILLED", actual_usd=ZERO)
        )
        return VoidedOperation(operation_key=key, released_usd=released, recorded=True)

    def _settle_job_usage(
        self, key: str, actual: Decimal, provider_usage: ProviderUsage
    ) -> UsageSettlement:
        probe = self._usage(key, lock=False)
        if probe is None or probe["background_job_id"] is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        job = self._job_for_usage(probe)
        usage = self._usage(key, lock=True)
        if usage is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        reserved = money(usage["estimated_max_usd"])
        if usage["status"] == "SETTLED" and usage["cost_kind"] not in (None, "ACTUAL"):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Non-actual billing requires explicit reconciliation."
            )
        if usage["status"] == "SETTLED":
            if not self._same_settlement(usage, actual, provider_usage):
                raise ServiceError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Settled provider usage cannot change."
                )
            return UsageSettlement(
                operation_key=key,
                actual_usd=actual,
                released_usd=ZERO,
                calls_consumed=usage["reserved_calls"],
                candidates_created=0,
                recorded=False,
                above_reservation=actual > reserved,
            )
        if usage["status"] == "UNKNOWN_CHARGE":
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED,
                "An unresolved provider charge must be resolved by an operator.",
            )
        if usage["status"] != "RESERVED" or usage["dispatched_at"] is None:
            raise ServiceError(ErrorCode.INVALID_STATE, "Only dispatched job usage can be settled.")
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(
                status="SETTLED",
                cost_kind="ACTUAL",
                actual_usd=actual,
                settled_at=func.now(),
                tokens_in=self._counter(provider_usage.tokens_in, label="tokens_in"),
                tokens_out=self._counter(provider_usage.tokens_out, label="tokens_out"),
                latency_ms=self._counter(provider_usage.latency_ms, label="latency_ms"),
                provider_request_id=provider_usage.provider_request_id,
            )
        )
        # The allowance keeps the conservative upper bound; it is never refunded.
        if actual > reserved:
            allowance = self.t.deployment_allowance
            self.db.execute(select(allowance).with_for_update()).one()
            self.db.execute(
                allowance.update().values(
                    consumed_usd=allowance.c.consumed_usd + actual - reserved,
                    updated_at=func.now(),
                )
            )
            self._stop_job(job["id"], "BUDGET_EXCEEDED")
        return UsageSettlement(
            operation_key=key,
            actual_usd=actual,
            released_usd=ZERO,
            calls_consumed=usage["reserved_calls"],
            candidates_created=0,
            recorded=True,
            above_reservation=actual > reserved,
        )

    def _unknown_job_usage(self, key: str, note: str) -> UnknownCharge:
        probe = self._usage(key, lock=False)
        if probe is None or probe["background_job_id"] is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        job = self._job_for_usage(probe)
        usage = self._usage(key, lock=True)
        if usage is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        consumed = money(usage["estimated_max_usd"])
        if usage["status"] == "UNKNOWN_CHARGE":
            return UnknownCharge(
                operation_key=key, consumed_usd=consumed, reason=note, recorded=False
            )
        if usage["status"] != "RESERVED" or usage["dispatched_at"] is None:
            raise ServiceError(
                ErrorCode.INVALID_STATE, "Only dispatched job usage can become unresolved."
            )
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(status="UNKNOWN_CHARGE", cost_kind="UNKNOWN")
        )
        self._stop_job(job["id"], "PROVIDER_OUTCOME_UNKNOWN")
        self._audit(
            action="JOB_UNKNOWN_CHARGE_RECORDED",
            actor="worker",
            entity_type="background_job",
            entity_id=str(job["id"]),
            project_id=job["project_id"],
            operation_key=key,
            reason=note,
            consumed_usd=str(consumed),
        )
        return UnknownCharge(operation_key=key, consumed_usd=consumed, reason=note, recorded=True)

    def _stop_job(self, job_id: UUID, error_code: str) -> None:
        jobs = self.t.background_jobs
        self.db.execute(
            jobs.update()
            .where(jobs.c.id == job_id)
            .values(
                state="REVIEW_REQUIRED",
                error_code=error_code,
                finished_at=func.now(),
                updated_at=func.now(),
            )
        )

    def settle_estimate(self, operation_key: str) -> None:
        """Consume a successful but unpriced response without inventing actual usage."""
        self._finish_nonactual(
            operation_key, kind="ESTIMATED", proof_code="MISSING_USAGE", request_sent=True
        )

    def finish_unbilled(self, operation_key: str, *, proof_code: str, request_sent: bool) -> None:
        """Close a claimed attempt only with trusted pre-send/rejection proof."""
        if (
            proof_code not in {"CONNECT_FAILED", "UNBILLED_RATE_LIMIT", "DEADLINE_BEFORE_SEND"}
            or type(request_sent) is not bool
            or request_sent != (proof_code == "UNBILLED_RATE_LIMIT")
        ):
            raise ServiceError(ErrorCode.INVALID_INPUT, "Unbilled proof is not recognized.", 422)
        self._finish_nonactual(
            operation_key, kind="UNBILLED", proof_code=proof_code, request_sent=request_sent
        )

    def _finish_nonactual(
        self, operation_key: str, *, kind: str, proof_code: str, request_sent: bool
    ) -> None:
        key = self._key(operation_key)
        probe = self._usage(key, lock=False)
        if probe is None:
            raise ServiceError(ErrorCode.NOT_FOUND, "Operation reservation was not found.", 404)
        experiment = None
        operation = None
        if probe["experiment_id"] is not None:
            experiment = self._experiment(probe["experiment_id"])
            operation = self._operation(key, lock=True)
        else:
            self._job_for_usage(probe)
        usage = self._usage(key, lock=True)
        assert usage is not None
        proof = {"kind": kind, "proof_code": proof_code, "request_sent": request_sent}
        if usage["status"] == "SETTLED":
            if (
                usage["cost_kind"] == kind
                and usage["metadata_json"].get("nonactual_proof") == proof
            ):
                return
            raise ServiceError(ErrorCode.IDEMPOTENCY_CONFLICT, "Recorded billing cannot change.")
        if (
            usage["status"] != "RESERVED"
            or usage["dispatched_at"] is None
            or (operation is not None and operation["status"] != "RUNNING")
        ):
            raise ServiceError(ErrorCode.INVALID_STATE, "Only a claimed attempt can be finalized.")
        reserved = money(usage["estimated_max_usd"])
        estimate = reserved if kind == "ESTIMATED" else ZERO
        self.db.execute(
            self.t.usage_events.update()
            .where(self.t.usage_events.c.operation_key == key)
            .values(
                status="SETTLED",
                cost_kind=kind,
                actual_usd=None if kind == "ESTIMATED" else ZERO,
                accounted_estimate_usd=estimate,
                settled_at=func.now(),
                metadata_json={**usage["metadata_json"], "nonactual_proof": proof},
            )
        )
        if experiment is None and kind == "UNBILLED":
            # Trusted rejection/pre-send proof releases the monetary hold once.
            # A sent 429 still consumes a call; failed connections consume none.
            allowance = self.t.deployment_allowance
            self.db.execute(select(allowance).with_for_update()).one()
            self.db.execute(
                allowance.update().values(
                    consumed_usd=allowance.c.consumed_usd - reserved,
                    consumed_calls=allowance.c.consumed_calls
                    - (0 if request_sent else usage["reserved_calls"]),
                    updated_at=func.now(),
                )
            )
        if experiment is not None and operation is not None:
            calls = operation["target_calls"] if request_sent else 0
            candidates = operation["candidate_slots"] if kind == "ESTIMATED" else 0
            self.db.execute(
                self.t.run_operations.update()
                .where(self.t.run_operations.c.operation_key == key)
                .values(
                    status="DONE" if kind == "ESTIMATED" else "FAILED",
                    finished_at=func.now(),
                    error_code=None if kind == "ESTIMATED" else proof_code,
                    result_json=proof,
                )
            )
            table = self.t.experiments
            self.db.execute(
                table.update()
                .where(table.c.id == experiment["id"])
                .values(
                    reserved_usd=func.greatest(table.c.reserved_usd - reserved, ZERO),
                    spent_usd=table.c.spent_usd + estimate,
                    calls_consumed=table.c.calls_consumed + calls,
                    candidates_created=table.c.candidates_created + candidates,
                )
            )
