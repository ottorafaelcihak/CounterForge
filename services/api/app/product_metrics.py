"""Owner read summaries from retained rows; never dispatch providers or estimate validity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select

if TYPE_CHECKING:
    from .service import Service


def stored_metric_details(service: Service, experiment_id: UUID) -> dict[str, Any]:
    db, tables = service.db, service.t
    canonical = select(tables.mutations.c.id).where(
        tables.mutations.c.experiment_id == experiment_id,
        tables.mutations.c.minimization_job_id.is_(None),
    )
    observations = tables.model_observations
    counts: dict[str, dict[str, int]] = {"SCREENING": {}, "CONFIRMATION": {}}
    for phase, outcome, number in db.execute(
        select(observations.c.phase, observations.c.outcome, func.count())
        .where(
            observations.c.experiment_id == experiment_id,
            observations.c.mutation_id.in_(canonical),
            observations.c.phase.in_(counts),
        )
        .group_by(observations.c.phase, observations.c.outcome)
    ):
        counts[phase][outcome] = number
    phases = {
        phase.lower(): {
            "total": sum(values.values()),
            "interpretable": values.get("CORRECT", 0) + values.get("INCORRECT", 0),
            "correct": values.get("CORRECT", 0),
            "incorrect": values.get("INCORRECT", 0),
            "unknown": values.get("UNKNOWN", 0),
            "provider_errors": values.get("PROVIDER_ERROR", 0),
        }
        for phase, values in counts.items()
    }
    findings = tables.findings
    finding_counts: dict[str, int] = {
        status: number
        for status, number in db.execute(
            select(findings.c.status, func.count())
            .where(findings.c.experiment_id == experiment_id, findings.c.mutation_id.in_(canonical))
            .group_by(findings.c.status)
        ).all()
    }
    usage = tables.usage_events
    spend: dict[str, dict[str, float]] = {}
    # Group in SQL to avoid transferring private usage rows or provider request IDs.
    for category, status, kind, actual, estimate, maximum in db.execute(
        select(
            usage.c.category,
            usage.c.status,
            usage.c.cost_kind,
            func.sum(usage.c.actual_usd),
            func.sum(usage.c.accounted_estimate_usd),
            func.sum(usage.c.estimated_max_usd),
        )
        .where(usage.c.experiment_id == experiment_id)
        .group_by(usage.c.category, usage.c.status, usage.c.cost_kind)
    ):
        values = spend.setdefault(
            category,
            {"actual_usd": 0.0, "estimated_usd": 0.0, "unresolved_usd": 0.0, "reserved_usd": 0.0},
        )
        if status == "SETTLED":
            if kind == "ACTUAL":
                values["actual_usd"] += float(actual or 0)
            elif kind == "ESTIMATED":
                values["estimated_usd"] += float(estimate or 0)
        elif status == "UNKNOWN_CHARGE":
            values["unresolved_usd"] += float(maximum or 0)
        elif status == "RESERVED":
            values["reserved_usd"] += float(maximum or 0)
    validations = tables.mutation_validations
    reports = db.execute(
        select(
            validations.c.mutation_id,
            validations.c.revision,
            validations.c.evidence_json["report_asset_id"].astext,
        )
        .where(validations.c.mutation_id.in_(canonical))
        .distinct(validations.c.mutation_id)
        .order_by(validations.c.mutation_id, validations.c.revision.desc(), validations.c.id.desc())
    )
    references = [
        {
            "mutation_id": mutation_id,
            "revision": revision,
            "report_asset_id": report_asset_id,
        }
        for mutation_id, revision, report_asset_id in reports
        if report_asset_id
    ]
    return {
        **phases,
        "finding_counts": finding_counts,
        "spend_by_category": spend,
        "report_references": references,
    }
