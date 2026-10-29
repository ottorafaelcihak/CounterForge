"""Matched configuration, predispatch declaration and exact all-cost denominators."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest
from counterforge_contracts.vtc import Limits
from sqlalchemy import select

from services.api.app.comparison import markdown, predeclare, report
from services.api.app.service import Service
from services.worker.app import experiments as runner
from tests.integrations.test_contracts_and_jobs import contract, target
from tests.integrations.test_experiment_pipeline import fixture_settings, replies

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def pair(api, payload):
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    _, _, vtc = await contract(api, payload)
    config = await target(api)
    ids = []
    limits = Limits(max_candidates=1, max_target_calls=7, max_estimated_usd=7.5)
    for mode in ("ADAPTIVE", "RANDOM_BASELINE"):
        response = await api.client.post(
            "/api/v1/experiments",
            json={
                "project_id": api.project_id,
                "contract_id": vtc["id"],
                "target_config_id": config["id"],
                "mode": mode,
                "limits": limits.model_dump(mode="json"),
                "random_seed": 20261030,
            },
            headers={"Idempotency-Key": "matched-" + mode},
        )
        assert response.status_code == 202, response.text
        ids.append(UUID(response.json()["experiment_id"]))
    return ids


async def test_matched_declaration_zero_arms_costs_and_no_retroactive_declaration(
    api, vtc_payload, monkeypatch
):
    ids = await pair(api, vtc_payload)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        manifest = predeclare(s, *ids, "test-source-revision")
        assert manifest["configuration"]["strength_range"] == [0, 1]
        assert manifest["arms"][1]["random_seed"] == 20261030
        zero = report(s, manifest)
        assert len(zero["arms"]) == 2 and all(a["stable_findings"] == 0 for a in zero["arms"])
        assert all(a["unfinished"] for a in zero["arms"])
        assert "RANDOM_BASELINE" in markdown(zero)
        tampered = deepcopy(manifest)
        tampered["configuration"]["limits"]["max_target_calls"] = 99
        with pytest.raises(ValueError, match="integrity"):
            report(s, tampered)
    replies(monkeypatch, [1, 1, 1, 1, 1, 1])
    for eid in ids:
        for _ in range(3):
            await runner.execute_experiment(api.factory, fixture_settings(api), eid)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        result = report(s, manifest)
        assert all(a["target_calls_consumed"] == 3 for a in result["arms"])
        assert all(
            a["observations_by_phase"]["SEED_BASELINE"]["observations"] == 3 for a in result["arms"]
        )
        assert all(a["target_requests_by_phase"]["SEED_BASELINE"] == 3 for a in result["arms"])
        assert all(a["usage"]["dispatched_requests"] == 3 for a in result["arms"])
        assert result["provenance"] == "LOCAL_FIXTURE"
        with pytest.raises(ValueError, match="Predeclare"):
            predeclare(s, *ids, "too-late")
        # Re-signed manifest still cannot move declaration after actual dispatch.
        from services.api.app.service import fingerprint

        late = deepcopy(manifest)
        late["declared_at"] = (
            uow.connection.scalar(select(__import__("sqlalchemy").func.now()))
            + timedelta(seconds=1)
        ).isoformat()
        late["manifest_sha256"] = fingerprint(
            {k: v for k, v in late.items() if k != "manifest_sha256"}
        )
        with pytest.raises(ValueError):
            report(s, late)
        changed = deepcopy(manifest)
        changed["configuration"]["target"]["model_revision"] = "changed-target"
        changed["manifest_sha256"] = fingerprint(
            {k: v for k, v in changed.items() if k != "manifest_sha256"}
        )
        with pytest.raises(ValueError, match="matched"):
            report(s, changed)


async def test_comparison_refuses_unequal_caps_and_records_exact_seed(api, vtc_payload):
    from counterforge_contracts.requests import StartExperiment

    ids = await pair(api, vtc_payload)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        original = s.experiment(ids[1])
        unequal = s.start_experiment(
            StartExperiment(
                project_id=original["project_id"],
                contract_id=original["contract_id"],
                target_config_id=original["target_config_id"],
                mode="RANDOM_BASELINE",
                random_seed=20261030,
                limits=Limits(max_candidates=1, max_target_calls=6, max_estimated_usd=7.5),
            ),
            "unequal-comparison",
        )
        with pytest.raises(ValueError, match="identical"):
            predeclare(s, ids[0], unequal.experiment_id, "test")
        assert original["config_snapshot"]["strategy"]["random_seed"] == 20261030


async def test_report_includes_rejected_planning_expenses_and_unknown_category_costs(
    api, vtc_payload
):
    from datetime import UTC, datetime
    from decimal import Decimal

    ids = await pair(api, vtc_payload)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        manifest = predeclare(s, *ids, "synthetic-ledger-coverage")
        now = datetime.now(UTC)
        for category, status, kind, cost, upper in [
            ("PLANNER", "SETTLED", "ACTUAL", "0.03", "1"),
            ("IMAGE", "UNKNOWN_CHARGE", "UNKNOWN", None, "0.012"),
            ("SAM", "SETTLED", "ESTIMATED", None, "0.003"),
            ("OTHER", "RESERVED", None, None, "0.02"),
        ]:
            s.db.execute(
                s.t.usage_events.insert().values(
                    experiment_id=ids[0],
                    operation_key="synthetic-comparison-" + category,
                    provider="FIXTURE",
                    model_id="LOCAL_FIXTURE",
                    category=category,
                    status=status,
                    cost_kind=kind,
                    actual_usd=Decimal(cost) if cost else None,
                    estimated_max_usd=Decimal(upper),
                    accounted_estimate_usd=Decimal("0.002") if kind == "ESTIMATED" else 0,
                    created_at=now,
                    dispatched_at=now if status != "RESERVED" else None,
                    metadata_json={
                        "synthetic": True,
                        "note": "Offline fabricated ledger coverage only",
                    },
                )
            )
        result = report(s, manifest)
        arm = result["arms"][0]
        assert arm["generated_candidates"] == 0 and arm["unfinished"]
        assert arm["usage"]["dispatched_requests"] == 3 and arm["unsettled_usage"]
        assert Decimal(arm["usage"]["actual_usd"]) == Decimal("0.03")
        assert Decimal(arm["usage"]["estimated_usd"]) == Decimal("0.002")
        assert Decimal(arm["usage"]["unknown_usd"]) == Decimal("0.012")
        assert Decimal(arm["usage"]["reserved_usd"]) == Decimal("0.02")
        assert arm["usage_by_category"]["IMAGE"]["dispatched_requests"] == 1
        assert result["arms"][1]["usage"]["dispatched_requests"] == 0


async def test_predeclare_blocks_a_preexisting_unbilled_random_plan(api, vtc_payload):
    ids = await pair(api, vtc_payload)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        s.db.execute(
            s.t.mutation_plans.insert().values(
                experiment_id=ids[1],
                planner_model="RANDOM_BASELINE",
                strategy="RANDOM",
                strategy_revision="random-initial-v2",
                hypothesis="Offline pre-existing-plan test",
                plan_json={},
                evidence_json={"synthetic": True},
            )
        )
        with pytest.raises(ValueError, match="planning"):
            predeclare(s, *ids, "too-late-for-plan")
