from decimal import Decimal

import pytest

from evaluations.report import ratio, summarize, write_bundle


def source():
    return dict(
        provenance="METHODOLOGY_FIXTURE",
        experiments=[
            dict(
                id="e",
                case_id="c",
                strategy="random",
                split="development",
                state="FAILED",
                stop_reason="OUTAGE",
                calls_consumed=1,
                max_target_calls=8,
            )
        ],
        usage=[
            dict(
                id="u",
                experiment_id="e",
                operation_key="op",
                category="TARGET",
                status="SETTLED",
                cost_kind="ACTUAL",
                actual_usd="0.01",
                estimated_max_usd="0.02",
                request_count=1,
                dispatched_at="2026-10-29T12:00:00+00:00",
                created_at="2026-10-29T12:00:00+00:00",
                latency_ms=100,
            )
        ],
        observations=[
            dict(
                id="o",
                experiment_id="e",
                operation_key="op",
                phase="SEED_BASELINE",
                outcome="CORRECT",
                latency_ms=100,
            )
        ],
        mutations=[],
        plans=[],
        findings=[],
        candidates=[],
        audits=[],
    )


def test_empty_denominators_and_censoring_with_all_usage(tmp_path):
    assert ratio(0, 0) == dict(numerator=0, denominator=0, value=None)
    result = summarize(source())
    arm = result["arms"][0]
    assert arm["stable_per_valid_screen"]["value"] is None
    assert arm["stable_per_paid_target"]["denominator"] == 1
    assert arm["first_discovery"]["censored"]
    assert arm["state"] == "FAILED"
    assert result["usage"]["actual_usd"] == "0.01"
    assert result["latency_ms"]["p95"] == 100
    hashes = write_bundle(tmp_path, source(), result)
    assert set(hashes) == {"raw.json", "report.json", "report.csv", "audit-sample.json"}


def test_reconciliation_duplicate_or_missing_usage_is_error():
    rows = source()
    rows["usage"].append(rows["usage"][0])
    with pytest.raises(ValueError, match="duplicate"):
        summarize(rows)
    rows = source()
    rows["usage"] = []
    with pytest.raises(ValueError, match="usage|reconcil"):
        summarize(rows)


def confirmed_source():
    from evaluations.fixture import fixture_source

    rows = fixture_source()
    for index, arm in enumerate(("random_det", "spark_det")):
        eid = f"methodology-{index}"
        mid = f"mutation-{index}"
        rows["experiments"][index]["calls_consumed"] = 7
        rows["experiments"][index]["ended_at"] = "2026-10-30T00:00:07+00:00"
        rows["plans"].append(dict(id="plan-" + mid, experiment_id=eid))
        rows["mutations"].append(
            dict(id=mid, experiment_id=eid, plan_id="plan-" + mid, image_asset_id="image-" + mid)
        )
        rows["candidates"].append(
            dict(
                candidate_id=mid,
                experiment_id=eid,
                strategy=arm,
                family="brightness_shift",
                image_sha256="a" * 64,
                evidence_sha256="b" * 64,
                candidate_sha256="c" * 64,
                original_verdict="VALID",
                stable=True,
                showcase=False,
            )
        )
        rows["findings"].append(
            dict(id="finding-" + mid, experiment_id=eid, mutation_id=mid, status="STABLE")
        )
        for attempt in range(4):
            phase = "SCREENING" if attempt == 0 else "CONFIRMATION"
            key = f"{mid}:{phase}:{attempt}"
            stamp = f"2026-10-30T00:00:0{attempt + 4}+00:00"
            rows["usage"].append(
                dict(
                    id=key,
                    experiment_id=eid,
                    operation_key=key,
                    category="TARGET",
                    status="SETTLED",
                    cost_kind="ACTUAL",
                    actual_usd="0.01",
                    estimated_max_usd="0.02",
                    request_count=1,
                    dispatched_at=stamp,
                    created_at=stamp,
                    latency_ms=100,
                )
            )
            rows["observations"].append(
                dict(
                    id=key,
                    experiment_id=eid,
                    operation_key=key,
                    mutation_id=mid,
                    phase=phase,
                    attempt_no=attempt if attempt else 1,
                    outcome="INCORRECT",
                    created_at=stamp,
                )
            )
    return rows


def test_audited_metrics_three_fresh_calls_censoring_and_cost_categories(tmp_path):
    from tests.unit.test_audit_sampling import decision

    rows = confirmed_source()
    before = summarize(rows)
    assert before["arms"][0]["stable_automatic"] == 1
    assert before["arms"][0]["stable_audited"] == 0
    rows["audits"] = [decision(c) for c in rows["candidates"]]
    after = summarize(rows)
    arm = after["arms"][0]
    assert arm["stable_per_100_valid_screens"] == dict(numerator=1, denominator=1, value=100)
    assert arm["stable_per_paid_target"] == dict(numerator=1, denominator=7, value=1 / 7)
    assert arm["stable_over_suspected"]["value"] == 1
    assert arm["first_discovery"] == dict(
        censored=False,
        target_calls=7,
        seconds=7.0,
        event_at="2026-10-30T00:00:07+00:00",
        definition="third fresh incorrect confirmation of independently passing finding",
    )
    assert after["primary_difference"] == dict(spark=1, random=1, difference=0, ratio=1)
    assert after["real_model_headline_findings"] == 0
    write_bundle(tmp_path, rows, after)
    from evaluations.report import verify_bundle

    verify_bundle(tmp_path)
    (tmp_path / "report.csv").write_text("tampered")
    with pytest.raises(ValueError, match="hash"):
        verify_bundle(tmp_path)


def test_baseline_failure_excludes_all_arms_and_retains_spend():
    from tests.unit.test_audit_sampling import decision

    rows = confirmed_source()
    rows["audits"] = [decision(c) for c in rows["candidates"]]
    rows["observations"][0]["outcome"] = "UNKNOWN"
    result = summarize(rows)
    assert all(a["excluded"] and a["stable_audited"] == 0 for a in result["arms"])
    assert result["usage"]["dispatched_requests"] == 17
    assert Decimal(result["usage"]["actual_usd"]) == Decimal("0.08")
