"""Only bounded allowlisted facts cross the planner boundary."""

import json
from copy import deepcopy

from counterforge_contracts.vtc import Limits, VisualTestContract
from counterforge_meta.spark import SparkPlanner

from tests.contracts.test_meta_adapters import Transport


async def test_planner_history_is_bounded_sanitized_and_does_not_rewrite_facts(vtc_payload):
    records = [
        dict(
            hypothesis="background hypothesis",
            family="brightness_shift",
            strength=0.2,
            validity="INVALID" if i % 2 else "VALID",
            reason_codes=["HUMAN_SEMANTICS"],
            target_outcomes=[dict(phase="SCREENING", outcome="UNKNOWN")],
            cost_usd=0.01,
            recipe_parameters={"level": 200, "private_url": "https://private/secret"},
            raw_response={"api_key": "private"},
            image="data:secret",
        )
        for i in range(15)
    ]
    records[-1]["hypothesis"] = "https://private/image?token=secret"
    original = deepcopy(records)
    transport = Transport(
        '{"family":"brightness_shift","strength":0.2,"hypothesis":"next","prompt":"background"}'
    )
    planner = SparkPlanner(transport=transport)
    await planner.plan(VisualTestContract.model_validate(vtc_payload), records, Limits())
    payload = json.loads(transport.calls[0]["messages"][1]["content"])
    safe = payload["recent_outcomes"]
    assert len(safe) == 10
    assert safe[-1]["hypothesis"] == "[redacted]"
    assert safe[-1]["validity"] == "VALID"
    assert safe[-1]["target_outcomes"] == [{"phase": "SCREENING", "outcome": "UNKNOWN"}]
    serialized = json.dumps(safe)
    assert all(s not in serialized for s in ["private", "data:", "api_key", "raw_response"])
    assert safe[0]["recipe_parameters"] == {"level": 200}
    assert records == original


async def test_large_safe_history_stays_within_prompt_budget(vtc_payload):
    records = [
        dict(
            hypothesis="word " * 100,
            family="brightness_shift",
            strength=0.2,
            validity="VALID",
            reason_codes=["Reason " * 14] * 20,
            target_outcomes=[dict(phase="CONFIRMATION", outcome="UNKNOWN")] * 20,
        )
        for _ in range(10)
    ]
    transport = Transport(
        '{"family":"brightness_shift","strength":0.2,"hypothesis":"next","prompt":"background"}'
    )
    await SparkPlanner(transport=transport).plan(
        VisualTestContract.model_validate(vtc_payload), records, Limits()
    )
    assert len(transport.calls[0]["messages"][1]["content"].encode()) < 32768


def test_embedded_key_in_hypothesis_is_redacted():
    from counterforge_meta.history import sanitized_history

    rows = sanitized_history(
        [dict(family="brightness_shift", strength=0.2, hypothesis="use sk-secretABCDEFG123456")]
    )
    assert rows[0]["hypothesis"] == "[redacted]"
