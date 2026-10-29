"""Experiment controls and transactional source-of-truth events; no dispatch here."""

from typing import Any
from uuid import UUID

from counterforge_contracts.responses import ExperimentRef, Finding, JobRef
from counterforge_contracts.versions import ErrorCode
from sqlalchemy import func, select

from .accounting import ACTIVE_STATES, Budget, money
from .errors import ServiceError

TERMINAL = frozenset(
    {"COMPLETED", "CANCELLED", "BUDGET_EXHAUSTED", "FAILED", "SEED_INVALID_FOR_SEARCH"}
)


def require_provider_mode(s: Any, row: Any) -> None:
    """A fixture or legacy intent cannot silently acquire paid execution rights."""
    pinned = row["config_snapshot"].get("provider_mode")
    if pinned not in {"DISABLED", "FIXTURE", "LIVE"} or pinned != s.settings.provider_mode:
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED,
            "The frozen provider mode does not match this deployment. Restore its mode or start a new experiment.",
        )


def transition(s: Any, eid: UUID, state: str, reason: str | None = None) -> None:
    s.db.execute(
        s.t.experiments.update()
        .where(s.t.experiments.c.id == eid)
        .values(
            state=state,
            stop_reason=reason,
            ended_at=func.now() if state in TERMINAL else None,
        )
    )
    event(s, eid, "EXPERIMENT_STATE_CHANGED", {"state": state, "reason": reason})


def event(s: Any, eid: UUID, kind: str, payload: dict[str, Any]) -> None:
    # Serialize ID assignment with commit order for this private stream.
    s.row(s.t.experiments, eid, lock=True)
    s.db.execute(
        s.t.experiment_events.insert().values(
            experiment_id=eid,
            event_type=kind,
            payload_json=payload,
        )
    )


def queue(s: Any, eid: UUID) -> None:
    # Shared advisory queue lock serializes starts and resumes at the final slot.
    s.db.execute(select(func.pg_advisory_xact_lock(73402031)))
    t = s.t.experiments
    row = s.row(t, eid, lock=True)
    if row["dispatch_pending"]:
        return
    count = s.db.scalar(
        select(func.count())
        .select_from(t)
        .where(
            (t.c.dispatch_pending | t.c.runner_token.is_not(None)),
            t.c.id != eid,
        )
    )
    jobs = s.t.background_jobs
    count += (
        s.db.scalar(
            select(func.count())
            .select_from(jobs)
            .where(jobs.c.state.in_(["QUEUED", "RUNNING"]), jobs.c.experiment_id.is_(None))
        )
        or 0
    )
    if count >= s.settings.max_pending_jobs:
        raise ServiceError(ErrorCode.QUEUE_FULL, "Experiment queue is at capacity.", 429)
    s.db.execute(t.update().where(t.c.id == eid).values(dispatch_pending=True))
    s.uow.new_experiment_ids.append(eid)


def control(s: Any, eid: UUID, action: str) -> ExperimentRef:
    row = s.row(s.t.experiments, eid, lock=True)
    s.project(row["project_id"])
    if action == "resume":
        if row["state"] != "PAUSED":
            raise ServiceError(ErrorCode.INVALID_STATE, "Only paused experiments can resume.")
        unknown = s.db.scalar(
            select(func.count())
            .select_from(s.t.usage_events)
            .where(
                s.t.usage_events.c.experiment_id == eid,
                s.t.usage_events.c.status == "UNKNOWN_CHARGE",
            )
        )
        if unknown or row["checkpoint_json"].get("unresolved_result"):
            raise ServiceError(
                ErrorCode.REVIEW_REQUIRED, "Resolve unknown charges before resuming."
            )
        state = row["checkpoint_json"].get("resume_state", "BASELINE_VALIDATING")
        if (Budget(s.uow)._now() - row["started_at"]).total_seconds() >= row["max_runtime_seconds"]:
            transition(s, eid, "BUDGET_EXHAUSTED", "RUNTIME_EXPIRED")
        else:
            transition(s, eid, state)
            queue(s, eid)
    else:
        if row["state"] in TERMINAL:
            return ExperimentRef.model_validate(s.experiment_ref(row))
        requested = "PAUSE_REQUESTED" if action == "pause" else "CANCEL_REQUESTED"
        final = "PAUSED" if action == "pause" else "CANCELLED"
        checkpoint = {
            **row["checkpoint_json"],
            "resume_state": row["state"]
            if row["state"] in ACTIVE_STATES
            else row["checkpoint_json"].get("resume_state", "BASELINE_VALIDATING"),
        }
        s.db.execute(
            s.t.experiments.update()
            .where(s.t.experiments.c.id == eid)
            .values(checkpoint_json=checkpoint)
        )
        transition(s, eid, requested, "OWNER_REQUEST")
        release_confirmation_holds(s, eid)
        if row["runner_token"] is None:
            transition(s, eid, final, "OWNER_REQUEST")
            s.db.execute(
                s.t.experiments.update()
                .where(s.t.experiments.c.id == eid)
                .values(dispatch_pending=False)
            )
    s.audit(row["project_id"], "EXPERIMENT_" + action.upper(), "experiment", eid)
    return ExperimentRef.model_validate(s.experiment_ref(s.experiment(eid)))


def canonical_confirmation(s: Any, finding_id: UUID) -> JobRef:
    f = s.row(s.t.findings, finding_id, lock=True)
    e = s.experiment(f["experiment_id"])
    if f["confirmation_job_id"] is not None:
        return JobRef.model_validate(s.job_dto(s.job(f["confirmation_job_id"])))
    s.require_valid_candidate(f["mutation_id"])
    jid = s.uow.background_jobs.enqueue(
        project_id=e["project_id"],
        experiment_id=e["id"],
        finding_id=finding_id,
        kind="CONFIRM",
        request_json={"finding_id": str(finding_id)},
        idempotency_key="canonical:" + str(finding_id),
    )
    s.db.execute(
        s.t.findings.update().where(s.t.findings.c.id == finding_id).values(confirmation_job_id=jid)
    )
    return JobRef.model_validate(s.job_dto(s.job(jid)))


def approve_demo(s: Any, finding_id: UUID) -> Finding:
    f = s.row(s.t.findings, finding_id, lock=True)
    s.finding(finding_id)
    report = s.require_valid_candidate(f["mutation_id"])
    if (
        f["status"] != "STABLE"
        or f["confirmation_attempts"] != 3
        or f["confirmation_failures"] != 3
    ):
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Demo approval requires a stable finding.")
    # Re-use the authoritative sealed report, never a caller supplied verdict.
    latest = s.candidates.latest(f["mutation_id"])
    digest = latest["evidence_json"]["report"]["report_sha256"]
    del report
    s.db.execute(
        s.t.findings.update()
        .where(s.t.findings.c.id == finding_id)
        .values(
            demo_approved_by=s.settings.owner_label,
            demo_approved_at=func.now(),
            demo_validation_sha256=digest,
        )
    )
    s.audit(
        s.experiment(f["experiment_id"])["project_id"],
        "FINDING_DEMO_APPROVED",
        "finding",
        finding_id,
        report_sha256=digest,
    )
    event(s, f["experiment_id"], "FINDING_DEMO_APPROVED", {"finding_id": str(finding_id)})
    return Finding.model_validate(s.finding_dto(s.finding(finding_id)))


def release_confirmation_holds(s: Any, eid: UUID) -> None:
    usage = s.t.usage_events
    keys = list(
        s.db.scalars(
            select(usage.c.operation_key).where(
                usage.c.experiment_id == eid,
                usage.c.status == "RESERVED",
                usage.c.dispatched_at.is_(None),
                usage.c.operation_key.like(f"{eid}:confirmation-batch:%"),
            )
        )
    )
    for key in keys:
        Budget(s.uow).void_operation(key)
    row = s.experiment(eid)
    if row["checkpoint_json"].get("confirmation_batch"):
        s.db.execute(
            s.t.experiments.update()
            .where(s.t.experiments.c.id == eid)
            .values(checkpoint_json={**row["checkpoint_json"], "confirmation_batch": None})
        )


def reserve_confirmation_batch(s: Any, e: Any, fid: UUID, count: int, upper: Any) -> None:
    """An unsent hold funds all remaining fresh slots before the first dispatch."""
    prior = e["checkpoint_json"].get("confirmation_batch")
    if prior:
        usage = (
            s.db.execute(
                select(s.t.usage_events).where(s.t.usage_events.c.operation_key == prior["key"])
            )
            .mappings()
            .one()
        )
        if usage["status"] == "RESERVED":
            return
    from decimal import Decimal

    key = f"{e['id']}:confirmation-batch:{fid}:{e['delivery_no']}:{count}"
    Budget(s.uow).reserve_operation(
        e["id"],
        key,
        "TARGET",
        money(upper) * count,
        target_calls=count,
        provider="BATCH_HOLD",
        model_id=e["config_snapshot"]["target"]["model_id"],
    )
    s.db.execute(
        s.t.experiments.update()
        .where(s.t.experiments.c.id == e["id"])
        .values(
            checkpoint_json={
                **e["checkpoint_json"],
                "confirmation_batch": {
                    "key": key,
                    "remaining": count,
                    "per_usd": str(Decimal(upper)),
                    "finding_id": str(fid),
                },
            }
        )
    )
