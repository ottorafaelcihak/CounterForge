"""Real PostgreSQL/fixture multi-batch search, never outbound provider traffic."""

from uuid import UUID

import pytest
from sqlalchemy import select

from services.worker.app import experiments as runner
from tests.integrations.test_candidates import fixture_scene, review_body
from tests.integrations.test_experiment_pipeline import replies, row

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def feedback_run(api, payload, cap=3):
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True
    original, _, settings, _, _ = await fixture_scene(api, payload)
    old = row(api, original)
    response = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": str(old["contract_id"]),
            "target_config_id": str(old["target_config_id"]),
            "search_policy": "FEEDBACK_V1",
            "random_seed": 7,
            "limits": {**old["config_snapshot"]["limits"], "max_candidates": cap},
        },
        headers={"Idempotency-Key": "feedback"},
    )
    assert response.status_code == 202, response.text
    return response.json()["experiment_id"], settings


async def drive(api, eid, settings, limit=50, invalid=False):
    for _ in range(limit):
        e = row(api, eid)
        if e["state"] in {"COMPLETED", "BUDGET_EXHAUSTED"}:
            return
        if e["state"] == "PAUSED":
            assert e["stop_reason"] in {
                "CANDIDATE_HUMAN_REVIEW_REQUIRED",
                "SCREENING_REVIEW_REQUIRED",
            }, e
            candidates = (await api.client.get(f"/api/v1/experiments/{eid}/mutations")).json()[
                "items"
            ]
            for m in candidates:
                if m["validation"]["verdict"] == "REVIEW_REQUIRED":
                    result = await api.client.post(
                        f"/api/v1/mutations/{m['id']}/review",
                        json=review_body(
                            m, **({"semantic": "additional_object"} if invalid else {})
                        ),
                    )
                    assert result.status_code == 200, result.text
            response = await api.client.post(f"/api/v1/experiments/{eid}/resume")
            assert response.status_code == 202, response.text
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    pytest.fail(f"feedback loop failed to terminate: {row(api, eid)}")


def distinct_planner_wire(monkeypatch):
    import json

    import httpx

    from services.worker.app.fixtures import mock_transport

    original = mock_transport()
    index = 0

    def handle(request):
        nonlocal index
        response = original.handle_request(request)
        if request.url.path == "/v1/chat/completions":
            data = response.json()
            proposal = json.loads(data["choices"][0]["message"]["content"])
            proposal["strength"] = 0.1 + 0.1 * index
            index += 1
            data["choices"][0]["message"]["content"] = json.dumps(proposal)
            return httpx.Response(200, json=data)
        return response

    monkeypatch.setattr(runner, "mock_transport", lambda: httpx.MockTransport(handle))


async def test_feedback_second_spark_batch_gets_actual_outcomes_and_unique_keys(
    api, vtc_payload, monkeypatch
):
    eid, settings = await feedback_run(api, vtc_payload, cap=4)
    replies(monkeypatch, [5, 5, 5, 5, 4, 4, 4, 4, "UNKNOWN", 5])
    distinct_planner_wire(monkeypatch)
    histories = []
    original = runner.SparkPlanner.plan

    async def capture(self, contract, history, remaining):
        histories.append((history, remaining))
        return await original(self, contract, history, remaining)

    monkeypatch.setattr(runner.SparkPlanner, "plan", capture)
    await drive(api, eid, settings)
    assert len(histories) == 3
    assert histories[0][0][0]["target_outcomes"] == [{"phase": "SCREENING", "outcome": "CORRECT"}]
    assert any(o["outcome"] == "INCORRECT" for h in histories[1][0] for o in h["target_outcomes"])
    assert any(o["outcome"] == "UNKNOWN" for h in histories[2][0] for o in h["target_outcomes"])
    view = (await api.client.get(f"/api/v1/experiments/{eid}")).json()
    assert view["search_policy"] == "FEEDBACK_V1"
    assert len(view["planning_batches"]) == 4
    assert view["planning_batches"][2]["history_snapshot"] == histories[1][0]
    assert view["planning_batches"][0]["slot_strategy"] == "RANDOM_EXPLORATION"
    assert row(api, eid)["checkpoint_json"]["slot_index"] == 4
    with api.factory() as uow:
        usages = (
            uow.connection.execute(
                select(uow.tables.usage_events).where(
                    uow.tables.usage_events.c.experiment_id == UUID(eid)
                )
            )
            .mappings()
            .all()
        )
        planners = [u for u in usages if u["category"] == "PLANNER"]
        assert len(planners) == 3 and len({u["operation_key"] for u in usages}) == len(usages)
        plans = (
            uow.connection.execute(
                select(uow.tables.mutation_plans).where(
                    uow.tables.mutation_plans.c.experiment_id == UUID(eid)
                )
            )
            .mappings()
            .all()
        )
        assert all(p["evidence_json"]["batch"]["rankings"] for p in plans)
        assert row(api, eid)["config_snapshot"]["strategy"]["revision"] == "feedback-v1"


async def test_feedback_invalid_candidates_are_bounded_and_rejection_history_retained(
    api, vtc_payload, monkeypatch
):
    eid, settings = await feedback_run(api, vtc_payload, cap=3)
    replies(monkeypatch, [5, 5, 5])
    histories = []
    original = runner.SparkPlanner.plan

    async def capture(self, contract, history, remaining):
        histories.append(history)
        return await original(self, contract, history, remaining)

    monkeypatch.setattr(runner.SparkPlanner, "plan", capture)
    await drive(api, eid, settings, invalid=True)
    assert len(histories) == 2
    assert histories[1][0]["validity"] == "INVALID"
    assert histories[1][0]["reason_codes"]
    assert row(api, eid)["calls_consumed"] == 3
    assert row(api, eid)["checkpoint_json"]["slot_index"] == 3


async def test_feedback_duplicate_images_consume_attempts_and_reserved_slots(
    api, vtc_payload, monkeypatch
):
    eid, settings = await feedback_run(api, vtc_payload, cap=7)
    replies(monkeypatch, [5, 5, 5, 5, 5])
    await drive(api, eid, settings, limit=70)
    e = row(api, eid)
    assert e["checkpoint_json"]["slot_index"] == 7
    assert e["candidates_created"] < 7
    view = (await api.client.get(f"/api/v1/experiments/{eid}")).json()
    batches = view["planning_batches"]
    assert [b["slot_index"] for b in batches if b["slot_strategy"] == "RANDOM_EXPLORATION"] == [
        0,
        5,
    ]
    assert batches[-1]["exploration_reserved"] == 2
    assert batches[-1]["exploration_used"] == 2
    assert batches[-1]["rankings"][0]["explore"] > 0
    assert "sam" in batches[-1]["cost_components"]
    assert batches[-1]["remaining_budget"]["max_candidates"] == 1
    with api.factory() as uow:
        usage = (
            uow.connection.execute(
                select(uow.tables.usage_events).where(
                    uow.tables.usage_events.c.experiment_id == UUID(eid)
                )
            )
            .mappings()
            .all()
        )
        assert len([u for u in usage if u["category"] == "PLANNER"]) == 5
        assert len([u for u in usage if u["category"] == "SAM"]) == e["candidates_created"]


async def test_feedback_planner_receipt_recovery_retains_proposal_without_resend(
    api, vtc_payload, monkeypatch
):
    eid, settings = await feedback_run(api, vtc_payload, cap=3)
    replies(monkeypatch, [5, 5, 5, 5])
    # Complete baseline and first reserved-random slot, stopping before Spark.
    for _ in range(20):
        e = row(api, eid)
        if e["checkpoint_json"].get("slot_index") == 1:
            break
        if e["state"] == "PAUSED":
            ms = (await api.client.get(f"/api/v1/experiments/{eid}/mutations")).json()["items"]
            for m in ms:
                if m["validation"]["verdict"] == "REVIEW_REQUIRED":
                    assert (
                        await api.client.post(
                            f"/api/v1/mutations/{m['id']}/review", json=review_body(m)
                        )
                    ).status_code == 200
            assert (await api.client.post(f"/api/v1/experiments/{eid}/resume")).status_code == 202
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    real_save = runner.save_checkpoint

    def crash_after_receipt(s, eid, **values):
        if values.get("plan_done"):
            raise RuntimeError("fixture crash after settled planner before checkpoint")
        return real_save(s, eid, **values)

    monkeypatch.setattr(runner, "save_checkpoint", crash_after_receipt)
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["state"] == "PAUSED"
    frozen_history = row(api, eid)["checkpoint_json"]["planning_context"]["history"]
    assert frozen_history and frozen_history[0]["validity"] == "VALID"
    # A new independent fixture review changes current history after dispatch.
    prior = (await api.client.get(f"/api/v1/experiments/{eid}/mutations")).json()["items"][0]
    changed = await api.client.post(
        f"/api/v1/mutations/{prior['id']}/review",
        json=review_body(prior, semantic="additional_object"),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["validation"]["verdict"] == "INVALID"
    monkeypatch.setattr(runner, "save_checkpoint", real_save)
    assert (await api.client.post(f"/api/v1/experiments/{eid}/resume")).status_code == 202
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        plans = (
            uow.connection.execute(
                select(uow.tables.mutation_plans).where(
                    uow.tables.mutation_plans.c.experiment_id == UUID(eid)
                )
            )
            .mappings()
            .all()
        )
        spark = [p for p in plans if p["evidence_json"]["batch"]["slot_index"] == 1]
        assert len(spark) == 1
        assert spark[0]["planner_model"] == "META_SPARK"
        assert spark[0]["evidence_json"]["batch"]["history_snapshot"] == frozen_history
        planners = (
            uow.connection.execute(
                select(uow.tables.usage_events).where(
                    uow.tables.usage_events.c.experiment_id == UUID(eid),
                    uow.tables.usage_events.c.category == "PLANNER",
                )
            )
            .mappings()
            .all()
        )
        assert len(planners) == 1


async def test_feedback_budget_one_is_random_only(api, vtc_payload, monkeypatch):
    eid, settings = await feedback_run(api, vtc_payload, cap=1)
    replies(monkeypatch, [5, 5, 5, 5])

    async def forbidden_planner(*args, **kwargs):
        pytest.fail("reserved slot must never call Spark")

    monkeypatch.setattr(runner.SparkPlanner, "plan", forbidden_planner)
    await drive(api, eid, settings)
    assert row(api, eid)["checkpoint_json"]["slot_index"] == 1
    assert row(api, eid)["state"] == "COMPLETED"


async def test_feedback_all_disallowed_proposals_retained_without_spending_random_quota(
    api, vtc_payload, monkeypatch
):
    from counterforge_contracts.plans import MutationPlan

    vtc_payload["allowed_mutations"] = ["background_replacement", "brightness_shift"]
    eid, settings = await feedback_run(api, vtc_payload, cap=3)
    replies(monkeypatch, [5, 5, 5, 5])

    async def disallowed(self, *args):
        self.last_evidence = {"author": "META_SPARK"}
        return [
            MutationPlan(
                family="background_clutter",
                strength=0.2,
                hypothesis="disallowed",
                prompt="background",
                requires_muse_image=True,
                estimated_cost_usd=0,
            )
        ]

    monkeypatch.setattr(runner.SparkPlanner, "plan", disallowed)
    await drive(api, eid, settings)
    e = row(api, eid)
    assert e["checkpoint_json"]["slot_index"] == 3
    assert e["candidates_created"] == 1
    view = (await api.client.get(f"/api/v1/experiments/{eid}")).json()
    assert len(view["planning_batches"]) == 3
    assert all(
        b["rankings"][0]["rejection_reason"] == "INVALID_PROPOSAL"
        for b in view["planning_batches"][1:]
    )
    assert all(not b["rankings"][0]["selected"] for b in view["planning_batches"][1:])


async def test_default_one_shot_preserves_legacy_request_fingerprint(api, vtc_payload):
    from counterforge_contracts.requests import StartExperiment

    from services.api.app.service import fingerprint
    from tests.integrations.test_candidates import experiment

    eid, *_ = await experiment(api, vtc_payload)
    e = row(api, eid)
    body = StartExperiment(
        project_id=e["project_id"],
        contract_id=e["contract_id"],
        target_config_id=e["target_config_id"],
    )
    legacy = body.model_dump(mode="json", exclude={"search_policy"})
    assert e["request_fingerprint"] == fingerprint(legacy)
    for request_body in (legacy, {**legacy, "search_policy": "ONE_SHOT"}):
        replay = await api.client.post(
            "/api/v1/experiments", json=request_body, headers={"Idempotency-Key": "candidates"}
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["experiment_id"] == eid
    conflict = await api.client.post(
        "/api/v1/experiments",
        json={**legacy, "search_policy": "FEEDBACK_V1"},
        headers={"Idempotency-Key": "candidates"},
    )
    assert conflict.status_code == 409, conflict.text


async def test_legacy_manual_plan_without_planning_evidence_remains_readable(api, vtc_payload):
    from tests.integrations.test_candidates import create, experiment

    eid, *_ = await experiment(api, vtc_payload)
    response = await create(api, eid)
    assert response.status_code == 201, response.text
    with api.factory() as uow:
        assert uow.connection.scalar(select(uow.tables.mutation_plans.c.evidence_json)) is None
    detail = await api.client.get(f"/api/v1/experiments/{eid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["planning_batches"] == []
    listing = await api.client.get(f"/api/v1/projects/{api.project_id}/experiments")
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["id"] == eid


async def stop_before_feedback_slot(api, eid, settings, slot):
    for _ in range(40):
        e = row(api, eid)
        if e["state"] == "PLANNING" and e["checkpoint_json"].get("slot_index") == slot:
            return
        if e["state"] == "PAUSED":
            for candidate in (await api.client.get(f"/api/v1/experiments/{eid}/mutations")).json()[
                "items"
            ]:
                if candidate["validation"]["verdict"] == "REVIEW_REQUIRED":
                    assert (
                        await api.client.post(
                            f"/api/v1/mutations/{candidate['id']}/review",
                            json=review_body(candidate),
                        )
                    ).status_code == 200
            assert (await api.client.post(f"/api/v1/experiments/{eid}/resume")).status_code == 202
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    pytest.fail("fixture did not reach requested feedback slot")


def expire_feedback_lease(api, eid):
    from datetime import timedelta

    from sqlalchemy import func

    with api.factory() as uow:
        t = uow.tables.experiments
        uow.connection.execute(
            t.update()
            .where(t.c.id == UUID(eid))
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()


@pytest.mark.parametrize("receipt", ["historical", "current"])
async def test_feedback_lease_recovery_reuses_settled_slot_receipts(
    api, vtc_payload, monkeypatch, receipt
):
    eid, settings = await feedback_run(api, vtc_payload, cap=3)
    replies(monkeypatch, [5] * 10)
    distinct_planner_wire(monkeypatch)
    await stop_before_feedback_slot(api, eid, settings, 2 if receipt == "historical" else 1)
    claim = runner.claim(api.factory, settings, UUID(eid))
    assert claim is not None
    if receipt == "current":
        original = runner.save_checkpoint

        def crash(s, eid, **values):
            if values.get("plan_done"):
                raise RuntimeError("simulated process loss after settled receipt")
            return original(s, eid, **values)

        monkeypatch.setattr(runner, "save_checkpoint", crash)
        with pytest.raises(RuntimeError, match="process loss"):
            await runner.plan_stage(api.factory, settings, claim)
        monkeypatch.setattr(runner, "save_checkpoint", original)

        async def never_resend(*args, **kwargs):
            pytest.fail("settled current planner receipt must not be resent")

        monkeypatch.setattr(runner.SparkPlanner, "plan", never_resend)
    expire_feedback_lease(api, eid)
    assert UUID(eid) in runner.recover_experiments(api.factory, settings)
    recovered = row(api, eid)
    assert recovered["state"] == "PLANNING"
    assert not recovered["checkpoint_json"].get("unresolved_result")
    # Owner pause/resume remains available; there is no permanently false gate.
    assert (await api.client.post(f"/api/v1/experiments/{eid}/pause")).status_code == 202
    assert (await api.client.post(f"/api/v1/experiments/{eid}/resume")).status_code == 202
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["checkpoint_json"]["plan_done"] is True
    with api.factory() as uow:
        events = (
            uow.connection.execute(
                select(uow.tables.usage_events).where(
                    uow.tables.usage_events.c.experiment_id == UUID(eid),
                    uow.tables.usage_events.c.category == "PLANNER",
                )
            )
            .mappings()
            .all()
        )
        assert len(events) == (2 if receipt == "historical" else 1)
        assert len([e for e in events if ":PLANNER_1:" in e["operation_key"]]) == 1


async def test_feedback_lease_recovery_never_retries_ambiguous_current_planner(
    api, vtc_payload, monkeypatch
):
    from decimal import Decimal

    from services.api.app.accounting import Budget

    eid, settings = await feedback_run(api, vtc_payload, cap=3)
    replies(monkeypatch, [5] * 10)
    await stop_before_feedback_slot(api, eid, settings, 1)
    assert runner.claim(api.factory, settings, UUID(eid)) is not None
    with api.factory() as uow:
        budget = Budget(uow)
        key = f"{eid}:PLANNER_1:seed:1"
        budget.reserve_operation(UUID(eid), key, "PLANNER", Decimal(0))
        assert budget.begin_operation(key)
        uow.commit()
    expire_feedback_lease(api, eid)
    assert UUID(eid) not in runner.recover_experiments(api.factory, settings)
    assert row(api, eid)["checkpoint_json"]["unresolved_result"] is True
    assert (await api.client.post(f"/api/v1/experiments/{eid}/resume")).status_code == 409
