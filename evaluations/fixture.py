"""Invented ledger rows for exercising methodology, NOT approved dataset or findings."""

from __future__ import annotations

from typing import Any


def fixture_source() -> dict[str, Any]:
    result: dict[str, Any] = dict(
        provenance="METHODOLOGY_FIXTURE",
        experiments=[],
        usage=[],
        observations=[],
        mutations=[],
        plans=[],
        findings=[],
        candidates=[],
        audits=[],
    )
    for i, arm in enumerate(("random_det", "spark_det", "full_system")):
        eid = f"methodology-{i}"
        result["experiments"].append(
            dict(
                id=eid,
                case_id="illustrative-ledger-only",
                split="development",
                strategy=arm,
                state="COMPLETED",
                stop_reason="NO_DISCOVERIES",
                calls_consumed=3,
                max_target_calls=8,
                started_at="2026-10-30T00:00:00+00:00",
                ended_at="2026-10-30T00:00:03+00:00",
            )
        )
        for attempt in range(1, 4):
            key = f"{eid}:baseline:{attempt}"
            stamp = f"2026-10-30T00:00:0{attempt}+00:00"
            result["usage"].append(
                dict(
                    id=key,
                    experiment_id=eid,
                    operation_key=key,
                    category="TARGET",
                    status="SETTLED",
                    cost_kind="ACTUAL",
                    actual_usd="0",
                    estimated_max_usd="0",
                    request_count=1,
                    dispatched_at=stamp,
                    created_at=stamp,
                    latency_ms=0,
                )
            )
            result["observations"].append(
                dict(
                    id=key,
                    experiment_id=eid,
                    operation_key=key,
                    phase="SEED_BASELINE",
                    attempt_no=attempt,
                    outcome="CORRECT",
                    created_at=stamp,
                )
            )
    return result
