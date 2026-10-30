"""Small predeclared equal-budget strategy comparison; no provider dispatch here."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

if TYPE_CHECKING:
    from .service import Service

MODES = ("ADAPTIVE", "RANDOM_BASELINE")
TERMINAL = {"COMPLETED", "BUDGET_EXHAUSTED", "CANCELLED", "SEED_INVALID_FOR_SEARCH", "FAILED"}


def shared_configuration(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compare immutable snapshots, including masks and independent validity policy."""
    from .service import fingerprint

    return {
        "seed": snapshot["seed"],
        "mask_sha256": sorted(item["sha256"] for item in snapshot["masks"]),
        "contract_sha256": snapshot["contract_sha256"],
        "target": snapshot["target"],
        "target_sha256": fingerprint(snapshot["target"]),
        "limits": snapshot["limits"],
        "provider_mode": snapshot["provider_mode"],
        "verifier_version": snapshot["verifier_version"],
        "policy_version": snapshot["policy_version"],
        "pricing_revision": snapshot["pricing_revision"],
        "allowed_mutations": snapshot["contract"]["allowed_mutations"],
        "strength_range": snapshot["strategy"]["strength_range"],
        "reproduction": snapshot["contract"]["reproduction"],
        "expected": snapshot["contract"]["expected"],
    }


def predeclare(
    s: Service, adaptive_id: UUID, random_id: UUID, code_revision: str
) -> dict[str, Any]:
    from .service import fingerprint

    ids = (adaptive_id, random_id)
    experiments = [s.experiment(eid) for eid in ids]
    if tuple(e["mode"] for e in experiments) != MODES or adaptive_id == random_id:
        raise ValueError("One distinct adaptive and random experiment are required")
    common = [shared_configuration(e["config_snapshot"]) for e in experiments]
    if common[0] != common[1]:
        raise ValueError("Comparison arms must have identical seed, masks, target, ranges and caps")
    if any(e["calls_consumed"] or e["candidates_created"] for e in experiments):
        raise ValueError("Predeclare before any observations or candidate generation")
    for eid in ids:
        if s.db.scalar(
            select(s.t.usage_events.c.id).where(s.t.usage_events.c.experiment_id == eid).limit(1)
        ):
            raise ValueError("Predeclare before any provider reservation")
    for eid in ids:
        if s.db.scalar(
            select(s.t.mutation_plans.c.id)
            .where(s.t.mutation_plans.c.experiment_id == eid)
            .limit(1)
        ):
            raise ValueError("Predeclare before any mutation planning")
    now = datetime.now(UTC).isoformat()
    result = {
        "format_version": "1.0.0",
        "declared_at": now,
        "code_revision": code_revision,
        "sample_size": 1,
        "configuration": common[0],
        "input_resolution": [
            s.asset(UUID(common[0]["seed"]["image_asset_id"]))[key] for key in ("width", "height")
        ],
        "arms": [
            {
                "mode": e["mode"],
                "experiment_id": str(e["id"]),
                "random_seed": e["config_snapshot"]["strategy"]["random_seed"],
                "strategy_revision": e["config_snapshot"]["strategy"]["revision"],
            }
            for e in experiments
        ],
        "limitations": [
            "One approved development seed per arm; no statistical superiority claim.",
            "Same available families and [0,1] strength range; Spark chooses strengths, random samples uniformly.",
            "Both arms retain the same independent validity and three-fresh confirmation gates.",
            "All planning, generation, SAM, baseline, screening and confirmation expenses are included.",
        ],
    }
    return {**result, "manifest_sha256": fingerprint(result)}


def summarize_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "request_slots_total": 0,
        "dispatched_requests": 0,
        "actual_usd": Decimal(0),
        "estimated_usd": Decimal(0),
        "unknown_usd": Decimal(0),
        "reserved_usd": Decimal(0),
    }
    for row in rows:
        totals["request_slots_total"] += row["request_count"]
        if row["dispatched_at"] is not None:
            totals["dispatched_requests"] += row["request_count"]
        if row["status"] == "SETTLED":
            if row["cost_kind"] == "ACTUAL":
                totals["actual_usd"] += row["actual_usd"] or Decimal(0)
            elif row["cost_kind"] == "ESTIMATED":
                totals["estimated_usd"] += row["accounted_estimate_usd"]
            elif row["cost_kind"] != "UNBILLED":
                totals["unknown_usd"] += row["estimated_max_usd"]
        elif row["status"] == "UNKNOWN_CHARGE":
            totals["unknown_usd"] += row["estimated_max_usd"]
        elif row["status"] == "RESERVED":
            totals["reserved_usd"] += row["estimated_max_usd"]
    return {
        key: str(value) if isinstance(value, Decimal) else value for key, value in totals.items()
    }


def report(s: Service, manifest: dict[str, Any]) -> dict[str, Any]:
    from .service import fingerprint

    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("format_version") != "1.0.0" or fingerprint(unsigned) != manifest.get(
        "manifest_sha256"
    ):
        raise ValueError("Comparison manifest integrity check failed")
    declared = datetime.fromisoformat(manifest["declared_at"])
    if declared.tzinfo is None or declared > datetime.now(UTC):
        raise ValueError("Comparison declaration time must be an aware past timestamp")
    if tuple(arm["mode"] for arm in manifest["arms"]) != MODES:
        raise ValueError("Both predeclared comparison arms are required")
    arms = []
    for arm in manifest["arms"]:
        eid = UUID(arm["experiment_id"])
        e = s.experiment(eid)
        if (
            e["mode"] != arm["mode"]
            or shared_configuration(e["config_snapshot"]) != manifest["configuration"]
        ):
            raise ValueError("Experiment differs from predeclared matched configuration")
        if [
            s.asset(UUID(e["config_snapshot"]["seed"]["image_asset_id"]))[key]
            for key in ("width", "height")
        ] != manifest["input_resolution"]:
            raise ValueError("Input resolution differs from the predeclared seed")
        strategy = e["config_snapshot"]["strategy"]
        if (
            strategy["random_seed"] != arm["random_seed"]
            or strategy["revision"] != arm["strategy_revision"]
        ):
            raise ValueError("Strategy differs from predeclared seed/revision")

        def rows(table: Any, experiment_id: UUID = eid) -> list[dict[str, Any]]:
            return [
                dict(r)
                for r in s.db.execute(
                    select(table).where(table.c.experiment_id == experiment_id)
                ).mappings()
            ]

        usage = rows(s.t.usage_events)
        if any(r["created_at"] < declared for r in usage):
            raise ValueError("Provider reservation predates comparison declaration")
        observations, mutations, findings, plans = (
            rows(table)
            for table in (s.t.model_observations, s.t.mutations, s.t.findings, s.t.mutation_plans)
        )
        # Followups share source evidence/caps, not discovery denominators.
        refinement_jobs = set(
            s.db.scalars(
                select(s.t.background_jobs.c.id).where(
                    s.t.background_jobs.c.experiment_id == eid,
                    s.t.background_jobs.c.kind == "MINIMIZE",
                )
            )
        )
        refinement_usage = [r for r in usage if r["background_job_id"] in refinement_jobs]
        source_usage = usage
        usage = [r for r in usage if r["background_job_id"] not in refinement_jobs]
        refined_ids = {m["id"] for m in mutations if m["minimization_job_id"] is not None}
        refined_plans = {m["plan_id"] for m in mutations if m["id"] in refined_ids}
        mutations = [m for m in mutations if m["id"] not in refined_ids]
        findings = [f for f in findings if f["mutation_id"] not in refined_ids]
        observations = [
            o
            for o in observations
            if o["retest_job_id"] is None and o["mutation_id"] not in refined_ids
        ]
        plans = [p for p in plans if p["id"] not in refined_plans]
        retest_jobs = s.db.scalars(
            select(s.t.background_jobs.c.id).where(
                s.t.background_jobs.c.kind == "RETEST",
                s.t.background_jobs.c.finding_id.in_(
                    select(s.t.findings.c.id).where(s.t.findings.c.experiment_id == eid)
                ),
            )
        ).all()
        retest_usage = [
            dict(r)
            for r in s.db.execute(
                select(s.t.usage_events).where(
                    s.t.usage_events.c.background_job_id.in_(retest_jobs)
                )
            ).mappings()
        ]
        if any(p["created_at"] < declared for p in plans):
            raise ValueError("Mutation planning predates comparison declaration")
        verdicts: Counter[str] = Counter()
        valid_ids = set()
        for mutation in mutations:
            current = s.candidates.checked_report(s.candidates.row(mutation["id"]))
            verdicts[current.verdict] += 1
            if current.verdict == "VALID":
                valid_ids.add(mutation["id"])
        phases = {
            phase: {
                "observations": len(group := [o for o in observations if o["phase"] == phase]),
                "outcomes": dict(Counter(o["outcome"] for o in group)),
            }
            for phase in ("SEED_BASELINE", "SCREENING", "CONFIRMATION")
        }
        total_usage = summarize_usage(usage)
        unsettled = any(r["status"] in {"RESERVED", "UNKNOWN_CHARGE"} for r in source_usage)
        arms.append(
            {
                **arm,
                "state": e["state"],
                "stop_reason": e["stop_reason"],
                "unfinished": e["state"] not in TERMINAL,
                "unsettled_usage": unsettled,
                "proposed_plans": len(plans),
                "generated_candidates": len(mutations),
                "candidate_validity": {
                    v: verdicts[v] for v in ("VALID", "INVALID", "REVIEW_REQUIRED")
                },
                "target_calls_consumed": e["calls_consumed"]
                - sum(
                    r["request_count"]
                    for r in refinement_usage
                    if r["category"] == "TARGET" and r["dispatched_at"] is not None
                ),
                "source_target_calls_consumed": e["calls_consumed"],
                "source_usage": summarize_usage(source_usage),
                "refinement_usage": summarize_usage(refinement_usage),
                "retest_usage": summarize_usage(retest_usage),
                "target_cap": e["max_target_calls"],
                "observations_by_phase": phases,
                "valid_screening_observations": sum(
                    o["phase"] == "SCREENING" and o["mutation_id"] in valid_ids
                    for o in observations
                ),
                "target_requests_by_phase": {
                    phase: summarize_usage(
                        [
                            r
                            for r in usage
                            if r["category"] == "TARGET"
                            and (":" + phase + "_") in r["operation_key"]
                        ]
                    )["dispatched_requests"]
                    for phase in ("SEED_BASELINE", "SCREENING", "CONFIRMATION")
                },
                "suspected_findings_total": len(findings),
                "stable_findings": sum(f["status"] == "STABLE" for f in findings),
                "finding_statuses": dict(Counter(f["status"] for f in findings)),
                "usage": total_usage,
                "usage_by_category": {
                    category: summarize_usage([r for r in usage if r["category"] == category])
                    for category in ("TARGET", "PLANNER", "IMAGE", "SAM", "OTHER")
                },
            }
        )
    return {
        "format_version": "1.0.0",
        "manifest_sha256": manifest["manifest_sha256"],
        "reported_at": datetime.now(UTC).isoformat(),
        "provenance": "LOCAL_FIXTURE"
        if manifest["configuration"]["provider_mode"] == "FIXTURE"
        else "REAL_PROVIDER",
        "arms": arms,
        "limitations": manifest["limitations"],
        "interpretation": "Descriptive tiny-sample comparison only; zero discoveries and unfinished arms are retained. No superiority claim.",
    }


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Phase 6 initial strategy comparison",
        "",
        "Provenance: **" + result["provenance"] + "**.",
        "",
        "| Arm | State | Generated | Valid / invalid / review | Target calls / cap | Baseline / screen / confirm | Suspected / stable | API requests | Actual USD |",
        "| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: |",
    ]
    for a in result["arms"]:
        v, p, u = a["candidate_validity"], a["observations_by_phase"], a["usage"]
        lines.append(
            f"| {a['mode']} | {a['state']} | {a['generated_candidates']} | {v['VALID']} / {v['INVALID']} / {v['REVIEW_REQUIRED']} | {a['target_calls_consumed']} / {a['target_cap']} | {p['SEED_BASELINE']['observations']} / {p['SCREENING']['observations']} / {p['CONFIRMATION']['observations']} | {a['suspected_findings_total']} / {a['stable_findings']} | {u['dispatched_requests']} | {u['actual_usd']} |"
        )
    for arm in result["arms"]:
        refinement = arm.get("refinement_usage", {})
        retest = arm.get("retest_usage", {})
        if refinement.get("request_slots_total", 0) or retest.get("request_slots_total", 0):
            lines.append(
                f"\n{arm['mode']} followups excluded from search discoveries/costs: "
                f"refinement {refinement['dispatched_requests']} requests, actual USD {refinement['actual_usd']}; "
                f"retest {retest['dispatched_requests']} requests, actual USD {retest['actual_usd']}. "
                "Estimated, reserved and unknown followup costs remain in the JSON report; "
                "source_usage and source_target_calls_consumed retain original cap exposure."
            )
    return "\n".join(
        [
            *lines,
            "",
            result["interpretation"],
            "",
            "Complete category costs, estimates, unresolved exposure, PRNG seeds, stop reasons and outcome denominators are retained in the companion JSON and declaration.",
            "",
            *["- " + item for item in result["limitations"]],
            "",
        ]
    )
