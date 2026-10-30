"""Read-only sanitized projections of immutable plans and current retained evidence."""

from typing import Any
from uuid import UUID

from counterforge_contracts.history import PlanningBatch
from counterforge_meta.history import sanitized_history
from sqlalchemy import select


def planner_history(s: Any, eid: UUID, *, limit: int = 10) -> list[dict[str, Any]]:
    m, p = s.t.mutations, s.t.mutation_plans
    rows = (
        s.db.execute(
            select(m, p.c.hypothesis)
            .join(p, p.c.id == m.c.plan_id)
            .where(m.c.experiment_id == eid, m.c.minimization_job_id.is_(None))
            .order_by(m.c.created_at.desc(), m.c.id.desc())
            .limit(limit)
        )
        .mappings()
        .all()
    )
    records = []
    for row in reversed(rows):
        latest = s.candidates.latest(row["id"], required=False)
        outcomes = (
            s.db.execute(
                select(s.t.model_observations.c.phase, s.t.model_observations.c.outcome)
                .where(
                    s.t.model_observations.c.mutation_id == row["id"],
                    s.t.model_observations.c.retest_job_id.is_(None),
                )
                .order_by(s.t.model_observations.c.created_at, s.t.model_observations.c.id)
            )
            .mappings()
            .all()
        )
        keys = s.db.scalars(
            select(s.t.model_observations.c.operation_key).where(
                s.t.model_observations.c.mutation_id == row["id"],
                s.t.model_observations.c.retest_job_id.is_(None),
            )
        ).all()
        jobs = s.db.scalars(
            select(s.t.background_jobs.c.id).where(
                s.t.background_jobs.c.experiment_id == eid,
                s.t.background_jobs.c.request_json["mutation_id"].astext == str(row["id"]),
            )
        ).all()
        background = row["evidence_json"].get("background_job_id")
        if background:
            jobs.append(UUID(background))
        usage = (
            s.db.execute(
                select(s.t.usage_events).where(
                    s.t.usage_events.c.operation_key.in_(keys)
                    | s.t.usage_events.c.background_job_id.in_(jobs)
                )
            )
            .mappings()
            .all()
        )
        records.append(
            dict(
                hypothesis=row["hypothesis"],
                family=row["family"],
                strength=float(row["strength"]),
                recipe_parameters=row["recipe_json"].get("parameters", {}),
                validity=latest["verdict"] if latest else None,
                reason_codes=latest["evidence_json"].get("report", {}).get("reason_codes", [])
                if latest
                else [],
                target_outcomes=[dict(o) for o in outcomes],
                cost_usd=float(
                    sum(
                        (
                            u["actual_usd"]
                            if u["actual_usd"] is not None
                            else u["estimated_max_usd"] or 0
                        )
                        for u in usage
                    )
                ),
            )
        )
    return sanitized_history(records, limit=limit, max_bytes=None)


def planning_batches(s: Any, eid: UUID) -> list[PlanningBatch]:
    rows = (
        s.db.execute(
            select(s.t.mutation_plans.c.evidence_json)
            .where(s.t.mutation_plans.c.experiment_id == eid)
            .order_by(s.t.mutation_plans.c.created_at, s.t.mutation_plans.c.id)
        )
        .scalars()
        .all()
    )
    batches = {}
    for evidence in rows:
        if isinstance(evidence, dict) and "batch" in evidence:
            batch = PlanningBatch.model_validate(evidence["batch"])
            batches[batch.slot_index] = batch
    return [batches[k] for k in sorted(batches)]
