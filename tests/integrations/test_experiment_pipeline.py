"""Exercise real scheduler, accounting, controls and persisted observations offline."""

from datetime import timedelta
from uuid import UUID

import pytest
from counterforge_contracts.providers import TargetResult
from sqlalchemy import func, select

from services.api.app.service import Service
from services.worker.app import experiments as runner
from tests.integrations.test_candidates import BRIGHT, create, experiment, review_body

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


@pytest.fixture(autouse=True)
def explicit_fixture_mode(api):
    """Pin fixture intent at creation, rather than change modes after starting."""
    api.settings.provider_mode = "FIXTURE"
    api.settings.allow_synthetic_fixtures = True


def fixture_settings(api):
    return api.settings.model_copy(
        update={"provider_mode": "FIXTURE", "allow_synthetic_fixtures": True}
    )


def replies(monkeypatch, values):
    # Explicit injected synthetic wire for this test's tiny retained seed. The
    # production fixture guard still requires the development fixture hash.
    monkeypatch.setattr(runner, "require_seed", lambda image: None)
    real_stage = runner.stage

    async def debug_stage(*args):
        try:
            return await real_stage(*args)
        except Exception:
            import traceback

            traceback.print_exc()
            raise

    monkeypatch.setattr(runner, "stage", debug_stage)
    sequence = iter(values)

    async def evaluate(transport, target, image, expected):
        value = next(sequence)
        parsed = value if type(value) is int else None
        outcome = "CORRECT" if parsed == expected else "INCORRECT" if parsed is not None else value
        return TargetResult(
            raw_text=str(value),
            parsed_answer=parsed,
            status=outcome,
            model_id="LOCAL_FIXTURE",
            version_claim="simulated",
            options=target["inference_options"],
            latency_ms=0,
            request_id=None,
            raw_asset_sha256="a" * 64,
        )

    monkeypatch.setattr(runner, "evaluate_target", evaluate)


def row(api, eid):
    with api.factory() as uow:
        return dict(Service(uow, api.settings).experiment(UUID(eid)))


async def test_fixture_run_cannot_become_paid_after_deployment_mode_change(
    api, vtc_payload, monkeypatch
):
    from pydantic import SecretStr

    eid, *_ = await experiment(api, vtc_payload)
    replies(monkeypatch, [1, 1, 1])
    for _ in range(3):
        await runner.execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    assert row(api, eid)["state"] == "READY"
    attempts = []

    def forbidden_live_client(**kwargs):
        attempts.append(True)
        raise AssertionError("A fixture intent must not construct a live dispatch client")

    monkeypatch.setattr(runner, "MetaTransport", forbidden_live_client)
    live = api.settings.model_copy(
        update={
            "provider_mode": "LIVE",
            "run_meta_live_tests": True,
            "model_api_key": SecretStr("not-a-real-key-mode-switch-regression"),
        }
    )
    await runner.execute_experiment(api.factory, live, UUID(eid))
    assert not attempts
    current = row(api, eid)
    assert current["state"] == "PAUSED"
    assert current["stop_reason"] == "PROVIDER_MODE_MISMATCH"
    assert current["calls_consumed"] == 3


async def test_provider_mode_is_rechecked_at_reservation_and_legacy_is_closed(api, vtc_payload):
    from decimal import Decimal

    from counterforge_meta.usage import CallIntent
    from pydantic import SecretStr

    from services.api.app.errors import ServiceError
    from services.api.app.experiments import require_provider_mode

    eid, *_ = await experiment(api, vtc_payload)
    job = runner.claim(api.factory, fixture_settings(api), UUID(eid))
    assert job is not None
    live = api.settings.model_copy(
        update={
            "provider_mode": "LIVE",
            "run_meta_live_tests": True,
            "model_api_key": SecretStr("not-a-real-key-mode-switch-regression"),
        }
    )
    port = runner.accounting(api.factory, live, job, "PLANNER")
    intent = CallIntent("muse-spark-1.3", "PLANNER", "a" * 64, "test-only", Decimal(1), 1)
    with pytest.raises(ServiceError, match="frozen provider mode"):
        await port.reserve(intent)
    with api.factory() as uow:
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events)) == 0
        service = Service(uow, api.settings)
        with pytest.raises(ServiceError, match="frozen provider mode"):
            require_provider_mode(service, {"config_snapshot": {"provider_dispatch_enabled": True}})
        assert service.experiment_dto(service.experiment(UUID(eid))).synthetic is True


@pytest.mark.parametrize(
    "values,state,count",
    [
        ([1, 1, 1], "READY", 3),
        ([0], "SEED_INVALID_FOR_SEARCH", 1),
        (["UNKNOWN"], "PAUSED", 1),
        (["PROVIDER_ERROR"], "PAUSED", 1),
    ],
)
async def test_three_distinct_baselines_and_wrong_seed_stops(
    api, vtc_payload, monkeypatch, values, state, count
):
    eid, *_ = await experiment(api, vtc_payload)
    replies(monkeypatch, values)
    settings = fixture_settings(api)
    for _ in range(count):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["state"] == state
    with api.factory() as uow:
        obs = uow.connection.execute(select(uow.tables.model_observations)).mappings().all()
        assert len(obs) == count and len({o["operation_key"] for o in obs}) == count
        assert {o["phase"] for o in obs} == {"SEED_BASELINE"}
        assert all(o["raw_output_asset_id"] and o["invocation_json"]["image_sha256"] for o in obs)
        usage = uow.connection.execute(select(uow.tables.usage_events)).mappings().all()
        assert len(usage) == count and all(
            u["experiment_id"] == UUID(eid) and u["background_job_id"] is None for u in usage
        )
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.mutation_plans)) == 0
        )
    assert row(api, eid)["calls_consumed"] == count


async def valid_candidate(api, vtc_payload):
    eid, *_ = await experiment(api, vtc_payload)
    mutation = (await create(api, eid, plan=BRIGHT)).json()
    reviewed = await api.client.post(
        f"/api/v1/mutations/{mutation['id']}/review", json=review_body(mutation)
    )
    assert reviewed.status_code == 200, reviewed.text
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == UUID(eid))
            .values(state="EVALUATING")
        )
        uow.commit()
    return eid, reviewed.json()


@pytest.mark.parametrize(
    "values,expected,wrong",
    [
        ([0, 0, 0], "STABLE", 3),
        ([0, 1, 0], "INTERMITTENT", 2),
        ([1, 1, 1], "NOT_REPRODUCED", 0),
        ([0, "UNKNOWN"], "REVIEW_REQUIRED", 1),
    ],
)
async def test_screen_excluded_three_fresh_canonical_confirmations(
    api, vtc_payload, monkeypatch, values, expected, wrong
):
    eid, m = await valid_candidate(api, vtc_payload)
    replies(monkeypatch, [0, *values])
    settings = fixture_settings(api)
    for _ in range(1 + len(values)):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        f = uow.connection.execute(select(uow.tables.findings)).mappings().one()
        assert f["status"] == expected and f["confirmation_failures"] == wrong
        assert f["confirmation_attempts"] == len(values)
        obs = (
            uow.connection.execute(
                select(uow.tables.model_observations).order_by(
                    uow.tables.model_observations.c.created_at
                )
            )
            .mappings()
            .all()
        )
        assert obs[0]["phase"] == "SCREENING"
        assert len({o["operation_key"] for o in obs}) == len(obs)
        assert len({o["request_fingerprint"] for o in obs}) == 1
        assert all(o["confirmation_job_id"] == f["confirmation_job_id"] for o in obs[1:])
        fid = f["id"]
    a = await api.client.post(f"/api/v1/findings/{fid}/confirm", json={})
    b = await api.client.post(f"/api/v1/findings/{fid}/confirm", json={})
    assert a.status_code == b.status_code == 202 and a.json()["job_id"] == b.json()["job_id"]
    assert row(api, eid)["calls_consumed"] == 1 + len(values)
    if expected == "STABLE":
        approved = await api.client.post(f"/api/v1/findings/{fid}/demo-approve", json={})
        assert approved.status_code == 200 and approved.json()["demo_approved"]
        mutation = (await api.client.get(f"/api/v1/mutations/{m['id']}")).json()
        assert (
            await api.client.post(
                f"/api/v1/mutations/{m['id']}/review",
                json=review_body(mutation, semantic="additional_object"),
            )
        ).status_code == 200
        with api.factory() as uow:
            f = uow.connection.execute(select(uow.tables.findings)).mappings().one()
            assert f["status"] == "REVIEW_REQUIRED" and f["demo_approved_at"] is None


async def test_confirmation_complete_batch_cap(api, vtc_payload, monkeypatch):
    eid, _ = await valid_candidate(api, vtc_payload)
    replies(monkeypatch, [0])
    settings = fixture_settings(api)
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        uow.connection.execute(uow.tables.experiments.update().values(max_target_calls=3))
        uow.commit()
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["state"] == "BUDGET_EXHAUSTED"
    assert row(api, eid)["stop_reason"] == "CONFIRMATION_TARGET_CALL_CAP"
    assert row(api, eid)["calls_consumed"] == 1


@pytest.mark.parametrize("verdict", ["REVIEW_REQUIRED", "INVALID"])
async def test_only_valid_candidates_dispatch(api, vtc_payload, monkeypatch, verdict):
    eid, *_ = await experiment(api, vtc_payload)
    m = (await create(api, eid, plan=BRIGHT)).json()
    if verdict == "INVALID":
        assert (
            await api.client.post(
                f"/api/v1/mutations/{m['id']}/review",
                json=review_body(m, semantic="removed_object"),
            )
        ).status_code == 200
    with api.factory() as uow:
        uow.connection.execute(uow.tables.experiments.update().values(state="EVALUATING"))
        uow.commit()
    replies(monkeypatch, [])
    await runner.execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    assert row(api, eid)["state"] == ("PAUSED" if verdict == "REVIEW_REQUIRED" else "COMPLETED")
    assert row(api, eid)["calls_consumed"] == 0


async def test_expired_resume_and_durable_replay(api, vtc_payload):
    from services.api.app.experiment_stream import frame, replay

    eid, *_ = await experiment(api, vtc_payload)
    assert UUID(eid) in runner.recover_experiments(api.factory, api.settings)
    assert (await api.client.post(f"/api/v1/experiments/{eid}/pause")).json()["state"] == "PAUSED"
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update().values(started_at=func.now() - timedelta(hours=3))
        )
        uow.commit()
    assert (await api.client.post(f"/api/v1/experiments/{eid}/resume")).json()[
        "state"
    ] == "BUDGET_EXHAUSTED"
    events, _ = replay(api.factory, api.settings, UUID(eid), 0)
    assert len(events) >= 4 and [e["id"] for e in events] == sorted({e["id"] for e in events})
    rest, _ = replay(api.factory, api.settings, UUID(eid), events[-2]["id"])
    assert [e["id"] for e in rest] == [events[-1]["id"]]
    assert "data: " in frame(rest[0])
    r = await api.client.get(
        f"/api/v1/experiments/{eid}/events", headers={"Last-Event-ID": str(events[-2]["id"])}
    )
    assert r.status_code == 200 and f"id: {events[-1]['id']}" in r.text


@pytest.mark.parametrize("phase", ["SEED_BASELINE", "SCREENING", "CONFIRMATION"])
async def test_recovery_reuses_finished_target_receipt_without_paid_replay(
    api, vtc_payload, monkeypatch, phase
):
    from decimal import Decimal

    from counterforge_meta.usage import CallIntent, Charge

    if phase == "SEED_BASELINE":
        eid, *_ = await experiment(api, vtc_payload)
        mid = None
    else:
        eid, mutation = await valid_candidate(api, vtc_payload)
        mid = UUID(mutation["id"])
        if phase == "CONFIRMATION":
            replies(monkeypatch, [0])
            await runner.execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    settings = fixture_settings(api)
    claimed = runner.claim(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        s = Service(uow, settings)
        image_id = (
            s.candidates.row(mid)["image_asset_id"]
            if mid
            else UUID(claimed["config_snapshot"]["seed"]["image_asset_id"])
        )
        invocation = runner.target_invocation(
            claimed["config_snapshot"], image_id, s.artifacts.read_verified(image_id)
        )
    port = runner.accounting(
        api.factory, settings, claimed, phase + "_1", mid, runner.fingerprint(invocation)
    )
    ticket = await port.reserve(
        CallIntent("LOCAL_FIXTURE", "TARGET", "c" * 64, "fixture", Decimal(0), 1)
    )
    assert await ticket.claim()
    await ticket.finish(
        "COMPLETED",
        Charge("ACTUAL", Decimal(0), True, "VERIFIED_USAGE"),
        {},
        {"choices": [{"message": {"content": "1"}}]},
    )
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update().values(
                lease_expires_at=func.now() - timedelta(seconds=1),
                checkpoint_json={"plan_done": True},
            )
        )
        uow.commit()
    recovered = runner.recover_experiments(api.factory, settings)
    assert UUID(eid) in recovered
    monkeypatch.setattr(runner, "require_seed", lambda image: None)

    async def forbidden(*args):
        pytest.fail("A finished target receipt must not dispatch another request")

    monkeypatch.setattr(runner, "evaluate_target", forbidden)
    before = row(api, eid)["calls_consumed"]
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["calls_consumed"] == before
    with api.factory() as uow:
        obs = (
            uow.connection.execute(
                select(uow.tables.model_observations).where(
                    uow.tables.model_observations.c.phase == phase
                )
            )
            .mappings()
            .one()
        )
        assert obs["outcome"] == "CORRECT" and obs["raw_output_asset_id"]


@pytest.mark.parametrize("sent", [False, True])
async def test_recovery_unsent_retry_or_sent_unknown_charge(api, vtc_payload, monkeypatch, sent):
    from decimal import Decimal

    from counterforge_meta.usage import CallIntent

    eid, *_ = await experiment(api, vtc_payload)
    settings = fixture_settings(api)
    claimed = runner.claim(api.factory, settings, UUID(eid))
    port = runner.accounting(api.factory, settings, claimed, "SEED_BASELINE_1")
    ticket = await port.reserve(
        CallIntent("LOCAL_FIXTURE", "TARGET", "c" * 64, "fixture", Decimal(0), 1)
    )
    if sent:
        assert await ticket.claim()
        ticket.release()
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update().values(
                lease_expires_at=func.now() - timedelta(seconds=1)
            )
        )
        uow.commit()
    ids = runner.recover_experiments(api.factory, settings)
    if sent:
        assert UUID(eid) not in ids
        assert row(api, eid)["state"] == "PAUSED"
        assert (await api.client.post(f"/api/v1/experiments/{eid}/resume")).status_code == 409
        assert row(api, eid)["calls_consumed"] == 1
    else:
        assert UUID(eid) in ids
        replies(monkeypatch, [1])
        await runner.execute_experiment(api.factory, settings, UUID(eid))
        assert row(api, eid)["calls_consumed"] == 1
        with api.factory() as uow:
            events = uow.connection.execute(select(uow.tables.usage_events)).mappings().all()
            assert sorted(e["status"] for e in events) == ["SETTLED", "VOID"]


async def test_cancel_during_confirmation_drains_without_next_dispatch(
    api, vtc_payload, monkeypatch
):
    eid, _ = await valid_candidate(api, vtc_payload)
    replies(monkeypatch, [0])
    settings = fixture_settings(api)
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    original = runner.evaluate_target

    async def cancel_in_flight(*args):
        response = await api.client.post(f"/api/v1/experiments/{eid}/cancel")
        assert response.json()["state"] == "CANCEL_REQUESTED"
        return await original(*args)

    replies(monkeypatch, [0])
    original = runner.evaluate_target
    monkeypatch.setattr(runner, "evaluate_target", cancel_in_flight)
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["state"] == "CANCELLED" and row(api, eid)["calls_consumed"] == 2
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["calls_consumed"] == 2
    with api.factory() as uow:
        f = uow.connection.execute(select(uow.tables.findings)).mappings().one()
        assert f["confirmation_attempts"] == 1 and f["status"] == "SUSPECTED"


async def test_unknown_uploaded_fixture_seed_fails_closed(api, vtc_payload):
    eid, *_ = await experiment(api, vtc_payload)
    await runner.execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    assert row(api, eid)["state"] == "PAUSED"
    assert row(api, eid)["calls_consumed"] == 0


async def test_adaptive_plan_generation_sam_and_review_pause_economic_provenance(api, vtc_payload):
    from tests.integrations.test_candidates import fixture_scene

    eid, _, settings, _, _ = await fixture_scene(api, vtc_payload)
    for _ in range(8):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    e = row(api, eid)
    assert e["state"] == "PAUSED" and e["stop_reason"] == "CANDIDATE_HUMAN_REVIEW_REQUIRED"
    assert e["calls_consumed"] == 3 and e["candidates_created"] == 1
    with api.factory() as uow:
        t = uow.tables
        plans = (
            uow.connection.execute(
                select(t.mutation_plans).where(
                    t.mutation_plans.c.strategy_revision == "spark-initial-v1"
                )
            )
            .mappings()
            .all()
        )
        assert len(plans) == 1 and plans[0]["planner_model"] == "META_SPARK"
        usage = (
            uow.connection.execute(
                select(t.usage_events).where(t.usage_events.c.experiment_id == UUID(eid))
            )
            .mappings()
            .all()
        )
        assert sorted(u["category"] for u in usage) == [
            "PLANNER",
            "SAM",
            "TARGET",
            "TARGET",
            "TARGET",
        ]
        assert all(u["raw_response_asset_id"] and u["pricing_revision"] for u in usage)
        assert all(u["status"] == "SETTLED" for u in usage)
        assert len({u["operation_key"] for u in usage}) == len(usage)


async def test_atomic_confirmation_hold_protects_final_slots_and_pause_releases(
    api, vtc_payload, monkeypatch
):
    from decimal import Decimal

    from services.api.app.accounting import Budget
    from services.api.app.errors import ServiceError

    eid, _ = await valid_candidate(api, vtc_payload)
    with api.factory() as uow:
        uow.connection.execute(uow.tables.experiments.update().values(max_target_calls=4))
        uow.commit()
    replies(monkeypatch, [0, 0])
    settings = fixture_settings(api)
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        assert Budget(uow)._pending(UUID(eid))[0] == 2
        with pytest.raises(ServiceError) as exc:
            Budget(uow).reserve_operation(
                UUID(eid), "competing-final-slot", "TARGET", Decimal(0), target_calls=1
            )
        assert str(exc.value.code) == "BUDGET_EXCEEDED"
    assert (await api.client.post(f"/api/v1/experiments/{eid}/pause")).json()["state"] == "PAUSED"
    with api.factory() as uow:
        assert Budget(uow)._pending(UUID(eid))[0] == 0
    assert row(api, eid)["calls_consumed"] == 2


@pytest.mark.parametrize(
    "boundary,reason",
    [("dollars", "CONFIRMATION_DOLLAR_CAP"), ("time", "CONFIRMATION_RUNTIME_CAP")],
)
async def test_confirmation_batch_cost_and_time_boundaries(api, vtc_payload, boundary, reason):
    from decimal import Decimal

    # Economic calculation only; pin its intended mode before creating the run.
    api.settings.provider_mode = "LIVE"
    eid, *_ = await experiment(api, vtc_payload)
    with api.factory() as uow:
        s = Service(uow, api.settings)
        e = dict(s.experiment(UUID(eid)))
        if boundary == "dollars":
            e["max_estimated_usd"] = Decimal(".001")
        else:
            e["started_at"] = runner.Budget(uow)._now() - timedelta(
                seconds=e["max_runtime_seconds"] - 100
            )
        assert runner.batch_fits(s, e, 3, runner.experiment_prices(e["config_snapshot"])) == reason


async def test_experiment_port_all_categories_retain_financial_attribution(api, vtc_payload):
    from decimal import Decimal

    from counterforge_meta.usage import CallIntent, Charge

    api.settings.provider_mode = "LIVE"
    eid, *_ = await experiment(api, vtc_payload)
    # Port-only settlement test: LIVE classification is economic provenance; no
    # transport/client is created and no key or external service is accessed.
    settings = api.settings.model_copy(update={"provider_mode": "LIVE"})
    claimed = runner.claim(api.factory, settings, UUID(eid))
    for category, model in [
        ("PLANNER", "muse-spark-1.3"),
        ("SAM", "sam-3.1"),
        ("IMAGE", "muse-image-1.0"),
        ("TARGET", "muse-spark-1.3"),
    ]:
        port = runner.accounting(api.factory, settings, claimed, category)
        ticket = await port.reserve(
            CallIntent(model, category, "a" * 64, "frozen-prices", Decimal(".1"), 1)
        )
        assert await ticket.claim()
        await ticket.finish(
            "COMPLETED",
            Charge("ACTUAL", Decimal(".02"), True, "VERIFIED_USAGE"),
            {},
            {"status": "completed"},
        )
    e = row(api, eid)
    assert e["spent_usd"] == Decimal(".08") and e["reserved_usd"] == 0
    assert e["calls_consumed"] == 1 and e["candidates_created"] == 0
    with api.factory() as uow:
        events = uow.connection.execute(select(uow.tables.usage_events)).mappings().all()
        assert len(events) == 4
        assert all(
            v["experiment_id"] == UUID(eid)
            and v["background_job_id"] is None
            and v["provider"] == "META"
            for v in events
        )
        assert all(v["raw_response_asset_id"] and v["cost_kind"] == "ACTUAL" for v in events)


async def test_queued_start_retry_and_combined_queue_cap(api, vtc_payload):
    from counterforge_contracts.requests import SegmentationRequest, StartExperiment

    from services.api.app.errors import ServiceError

    eid, seed, _, vtc = await experiment(api, vtc_payload)
    e = row(api, eid)
    body = StartExperiment(
        project_id=api.project_id, contract_id=vtc["id"], target_config_id=e["target_config_id"]
    )
    settings = api.settings.model_copy(update={"max_pending_jobs": 1})
    with api.factory() as uow:
        s = Service(uow, settings)
        uow.background_jobs.max_pending_jobs = 1
        with pytest.raises(ServiceError) as exc:
            s.start_experiment(body, "queue-overflow")
        assert str(exc.value.code) == "QUEUE_FULL"
    with api.factory() as uow:
        s = Service(uow, settings)
        uow.background_jobs.max_pending_jobs = 1
        with pytest.raises(ServiceError) as exc:
            s.segment(
                UUID(seed["id"]), SegmentationRequest(concept="red container"), "competing-setup"
            )
        assert str(exc.value.code) == "QUEUE_FULL"
    assert UUID(eid) in runner.recover_experiments(api.factory, settings)


async def test_late_reply_preserves_terminal_budget_state(api, vtc_payload, monkeypatch):
    eid, *_ = await experiment(api, vtc_payload)
    replies(monkeypatch, [1])
    original = runner.evaluate_target

    async def late(*args):
        with api.factory() as uow:
            uow.connection.execute(
                uow.tables.experiments.update().values(
                    state="BUDGET_EXHAUSTED", stop_reason="ACTUAL_COST_CAP"
                )
            )
            uow.commit()
        return await original(*args)

    monkeypatch.setattr(runner, "evaluate_target", late)
    await runner.execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    assert row(api, eid)["state"] == "BUDGET_EXHAUSTED"
    assert row(api, eid)["stop_reason"] == "ACTUAL_COST_CAP"
    with api.factory() as uow:
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.model_observations))
            == 1
        )


async def test_inflight_validity_change_keeps_reply_without_promotion(
    api, vtc_payload, monkeypatch
):
    eid, mutation = await valid_candidate(api, vtc_payload)
    replies(monkeypatch, [0, 0, 0, 0])
    settings = fixture_settings(api)
    for _ in range(3):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    original = runner.evaluate_target

    async def invalidate(*args):
        current = (await api.client.get(f"/api/v1/mutations/{mutation['id']}")).json()
        reviewed = await api.client.post(
            f"/api/v1/mutations/{mutation['id']}/review",
            json=review_body(current, semantic="removed_object"),
        )
        assert reviewed.status_code == 200
        return await original(*args)

    monkeypatch.setattr(runner, "evaluate_target", invalidate)
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        f = uow.connection.execute(select(uow.tables.findings)).mappings().one()
        assert f["status"] == "REVIEW_REQUIRED" and f["demo_approved_at"] is None
        assert f["confirmation_attempts"] == 3
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.model_observations))
            == 4
        )
        assert all(
            uow.connection.scalars(select(uow.tables.model_observations.c.raw_output_asset_id))
        )


async def test_sse_private_and_idle_keepalive(api, vtc_payload, monkeypatch):
    from types import SimpleNamespace

    from services.api.app import experiment_stream

    eid, *_ = await experiment(api, vtc_payload)
    denied = await api.client.get(
        f"/api/v1/experiments/{eid}/events", headers={"Authorization": ""}
    )
    assert denied.status_code == 401
    events, _ = experiment_stream.replay(api.factory, api.settings, UUID(eid), 0)
    times = iter([0, 16])
    monkeypatch.setattr(experiment_stream, "time", SimpleNamespace(monotonic=lambda: next(times)))

    class Request:
        async def is_disconnected(self):
            return False

    stream = experiment_stream.stream(
        Request(), api.factory, api.settings, UUID(eid), events[-1]["id"]
    )
    assert await anext(stream) == ": keepalive\n\n"
    await stream.aclose()
    history = await api.client.get(
        f"/api/v1/experiments/{eid}/event-history?after={events[-1]['id'] - 1}"
    )
    assert history.status_code == 200 and history.json()[-1]["id"] == events[-1]["id"]


async def test_duplicate_delivery_is_one_actual_attempt(api, vtc_payload, monkeypatch):
    eid, *_ = await experiment(api, vtc_payload)
    replies(monkeypatch, [1])
    original = runner.evaluate_target

    async def duplicate(*args):
        await runner.execute_experiment(api.factory, fixture_settings(api), UUID(eid))
        return await original(*args)

    monkeypatch.setattr(runner, "evaluate_target", duplicate)
    await runner.execute_experiment(api.factory, fixture_settings(api), UUID(eid))
    assert row(api, eid)["calls_consumed"] == 1
    with api.factory() as uow:
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.model_observations))
            == 1
        )


async def test_experiment_image_and_sam_use_experiment_reservations(api, vtc_payload):
    from tests.integrations.test_candidates import fixture_scene

    eid, _, settings, plan, _ = await fixture_scene(api, vtc_payload)
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == UUID(eid))
            .values(state="MUTATING", checkpoint_json={"plan_done": True, "plans": [plan]})
        )
        uow.commit()
    for _ in range(3):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    assert row(api, eid)["state"] == "PAUSED"
    assert row(api, eid)["candidates_created"] == 1
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
        assert sorted(v["category"] for v in usage) == ["IMAGE", "SAM"]
        assert all(
            v["background_job_id"] and v["raw_response_asset_id"] and v["status"] == "SETTLED"
            for v in usage
        )
        for v in usage:
            job = Service(uow, settings).job(v["background_job_id"])
            assert job["experiment_id"] == UUID(eid)
            if v["category"] == "IMAGE":
                # Identical provider bytes share an immutable blob/first metadata.
                # Per-attempt geometry/provenance belongs to its durable job result.
                geometry = job["result_json"]["background_geometry"]
                assert geometry["preparatory_usage"] is False
                assert geometry["experiment_id"] == eid
                assert geometry["policy"].startswith("background-cover-center-bicubic-v1")


async def test_spark_fallback_is_honest_one_shot(api, vtc_payload, monkeypatch):
    import httpx

    from tests.integrations.test_candidates import fixture_scene

    eid, _, settings, _, _ = await fixture_scene(api, vtc_payload)
    for _ in range(3):
        await runner.execute_experiment(api.factory, settings, UUID(eid))

    def invalid(request):
        return httpx.Response(
            200,
            json={
                "id": "synthetic-invalid",
                "model": "muse-spark-1.3",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "invalid proposal"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    monkeypatch.setattr(runner, "mock_transport", lambda: httpx.MockTransport(invalid))
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        t = uow.tables
        plan = (
            uow.connection.execute(
                select(t.mutation_plans).where(
                    t.mutation_plans.c.strategy_revision == "spark-initial-v1"
                )
            )
            .mappings()
            .one()
        )
        assert plan["planner_model"] == "DETERMINISTIC_FALLBACK"
        assert plan["evidence_json"]["fallback_reason"] == "INVALID"
        assert plan["planner_response_asset_id"]
        uow.connection.execute(
            t.experiments.update().where(t.experiments.c.id == UUID(eid)).values(state="PLANNING")
        )
        uow.commit()
    await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        assert (
            uow.connection.scalar(
                select(func.count())
                .select_from(uow.tables.usage_events)
                .where(
                    uow.tables.usage_events.c.experiment_id == UUID(eid),
                    uow.tables.usage_events.c.category == "PLANNER",
                )
            )
            == 1
        )


async def test_random_baseline_skips_spark_but_retains_bounded_plan(api, vtc_payload):
    from tests.integrations.test_candidates import fixture_scene

    eid, _, settings, _, _ = await fixture_scene(api, vtc_payload)
    e = row(api, eid)
    response = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": str(e["contract_id"]),
            "target_config_id": str(e["target_config_id"]),
            "mode": "RANDOM_BASELINE",
        },
        headers={"Idempotency-Key": "random-run"},
    )
    assert response.status_code == 202
    eid = response.json()["experiment_id"]
    for _ in range(4):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        t = uow.tables
        plan = (
            uow.connection.execute(
                select(t.mutation_plans).where(t.mutation_plans.c.experiment_id == UUID(eid))
            )
            .mappings()
            .one()
        )
        assert plan["strategy"] == "RANDOM" and plan["strategy_revision"] == "random-initial-v2"
        assert plan["evidence_json"]["author"] == "RANDOM_BASELINE"
        assert (
            uow.connection.scalar(
                select(func.count())
                .select_from(t.usage_events)
                .where(
                    t.usage_events.c.experiment_id == UUID(eid),
                    t.usage_events.c.category == "PLANNER",
                )
            )
            == 0
        )


@pytest.mark.parametrize(
    "outcome,raw,expected_status,bound",
    [
        ("COMPLETED", {"choices": [{"message": {"content": "1"}}]}, "CORRECT", True),
        (
            "COMPLETED",
            {
                "response": {"choices": [{"message": {"content": "1"}}]},
                "template_version": "target-count-v1",
            },
            "CORRECT",
            True,
        ),
        ("PROVIDER_ERROR", {"error": {"code": "test"}}, "PROVIDER_ERROR", True),
        ("COMPLETED", {"choices": [{"message": {"content": None}}]}, "UNKNOWN", True),
        ("COMPLETED", {"choices": []}, "UNKNOWN", True),
        ("REFUSED", {"choices": [{"message": {"content": "1"}}]}, "UNKNOWN", True),
        ("COMPLETED", {"choices": [{"message": {"content": "1"}}]}, None, False),
    ],
)
async def test_meta_receipt_replay_preserves_adapter_evidence_and_binding(
    api, vtc_payload, monkeypatch, outcome, raw, expected_status, bound
):
    from decimal import Decimal
    from hashlib import sha256

    from counterforge_meta.usage import CallIntent, Charge

    from services.api.app.accounting import Budget
    from services.api.app.service import fingerprint
    from tests.integrations.test_contracts_and_jobs import contract

    _, _, vtc = await contract(api, vtc_payload)
    target = await api.client.post(
        f"/api/v1/projects/{api.project_id}/targets",
        json={
            "adapter_key": "META_SPARK",
            "model_id": "muse-spark-1.3",
            "model_revision": "caller-claim-is-not-observed",
            "prompt_template": "Return an integer count.",
        },
    )
    started = await api.client.post(
        "/api/v1/experiments",
        headers={"Idempotency-Key": "replay-meta"},
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": target.json()["id"],
        },
    )
    eid = UUID(started.json()["experiment_id"])
    settings = fixture_settings(api)
    job = runner.claim(api.factory, settings, eid)
    port = runner.accounting(api.factory, settings, job, "SEED_BASELINE_1")
    ticket = await port.reserve(
        CallIntent("muse-spark-1.3", "TARGET", "c" * 64, "fixture", Decimal(0), 1)
    )
    assert await ticket.claim()
    await ticket.finish(outcome, Charge("ACTUAL", Decimal(0), True, "VERIFIED_USAGE"), {}, raw)
    with api.factory() as uow:
        s = Service(uow, settings)
        snap = job["config_snapshot"]
        image_id = UUID(snap["seed"]["image_asset_id"])
        invocation = {
            "image_asset_id": str(image_id),
            "image_sha256": sha256(s.artifacts.read_verified(image_id)).hexdigest(),
            "prompt": snap["target"]["prompt_template"],
            "prompt_sha256": sha256(snap["target"]["prompt_template"].encode()).hexdigest(),
            "target": snap["target"],
            "adapter_version": snap["adapter_version"],
            "verifier_version": snap["verifier_version"],
            "pricing_revision": snap["pricing_revision"],
        }
        usage = uow.tables.usage_events
        row = (
            uow.connection.execute(select(usage).where(usage.c.operation_key == ticket.key))
            .mappings()
            .one()
        )
        uow.connection.execute(
            usage.update()
            .where(usage.c.id == row["id"])
            .values(
                metadata_json={
                    **row["metadata_json"],
                    "invocation_sha256": fingerprint(invocation) if bound else "d" * 64,
                }
            )
        )
        uow.commit()
    # An unsent later reservation must not capture the retained observation.
    later = await port.reserve(
        CallIntent("muse-spark-1.3", "TARGET", "c" * 64, "fixture", Decimal(0), 2)
    )
    with api.factory() as uow:
        Budget(uow).void_operation(later.key)
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == eid)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        uow.commit()
    runner.recover_experiments(api.factory, settings)
    monkeypatch.setattr(runner, "require_seed", lambda image: None)

    async def forbidden(*args):
        pytest.fail("Recovered target receipt must never redispatch")

    monkeypatch.setattr(runner, "evaluate_target", forbidden)
    await runner.execute_experiment(api.factory, settings, eid)
    with api.factory() as uow:
        s = Service(uow, settings)
        obs = uow.connection.execute(select(uow.tables.model_observations)).mappings().all()
        if not bound:
            assert not obs and s.experiment(eid)["state"] == "PAUSED"
        else:
            assert len(obs) == 1
            assert obs[0]["outcome"] == expected_status
            assert obs[0]["model_version_observed"] == "provider-managed"
            assert obs[0]["sampling_config"]["max_output_tokens"] == 64
            assert obs[0]["operation_key"] == ticket.key
            assert obs[0]["raw_output_asset_id"] == row["raw_response_asset_id"]


@pytest.mark.parametrize("action", ["incomplete_sam", "cancel_after_sam"])
async def test_supporting_sam_uncertainty_pauses_for_candidate_review_without_replay(
    api, vtc_payload, monkeypatch, action
):
    from counterforge_contracts.providers import SegmentationResult

    from services.api.app.experiments import control
    from tests.integrations.test_candidates import fixture_scene

    eid, _, settings, plan, _ = await fixture_scene(api, vtc_payload)
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == UUID(eid))
            .values(state="MUTATING", checkpoint_json={"plan_done": True, "plans": [plan]})
        )
        uow.commit()
    if action == "incomplete_sam":

        async def empty_sam(*args):
            return SegmentationResult(
                objects=[], outcome="INCOMPLETE", diagnostics=("SAM_MISSING_INSTANCES",)
            )

        monkeypatch.setattr("services.worker.app.candidate_jobs.SamSegmenter.segment", empty_sam)
    else:
        original = runner.preparation

        async def cancel_after_sam(*args):
            jid = await original(*args)
            if args[3] == "MUTATION_SAM":
                with api.factory() as uow:
                    control(Service(uow, settings), UUID(eid), "cancel")
                    uow.commit()
            return jid

        monkeypatch.setattr(runner, "preparation", cancel_after_sam)
    for _ in range(3):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    result = row(api, eid)
    assert result["checkpoint_json"].get("sam_done") is True
    if action == "cancel_after_sam":
        assert result["state"] == "CANCELLED"
    else:
        assert (
            result["state"] == "PAUSED"
            and result["stop_reason"] == "CANDIDATE_HUMAN_REVIEW_REQUIRED"
        )
    assert result["calls_consumed"] == 0


async def test_pipeline_candidate_links_the_actual_retained_planner_proposal(api, vtc_payload):
    from tests.integrations.test_candidates import fixture_scene

    eid, _, settings, _, _ = await fixture_scene(api, vtc_payload)
    for _ in range(5):
        await runner.execute_experiment(api.factory, settings, UUID(eid))
    with api.factory() as uow:
        plans, mutations = uow.tables.mutation_plans, uow.tables.mutations
        linked = (
            uow.connection.execute(select(plans).join(mutations, mutations.c.plan_id == plans.c.id))
            .mappings()
            .one()
        )
        assert linked["planner_model"] == "META_SPARK"
        assert linked["planner_response_asset_id"] is not None
        assert linked["evidence_json"]["author"] == "META_SPARK"
        assert linked["evidence_json"]["max_completion_tokens"] == 4096
        assert linked["evidence_json"]["reasoning_effort"] == "low"
        assert row(api, eid)["checkpoint_json"]["source_plan_id"] == str(linked["id"])
    from counterforge_contracts.plans import MutationPlan
    from counterforge_contracts.requests import CreateMutation, StartExperiment

    from services.api.app.errors import ServiceError

    with api.factory() as uow:
        service = Service(uow, settings)
        changed = {**linked["plan_json"], "strength": 0.4}
        with pytest.raises(ServiceError, match="differs from retained"):
            service.candidates.create(
                UUID(eid),
                CreateMutation(plan=MutationPlan.model_validate(changed), rng_seed=17),
                "wrong-proposal",
                source_plan_id=linked["id"],
                automated=True,
            )
    with api.factory() as uow:
        service = Service(uow, settings)
        original = service.experiment(UUID(eid))
        other = service.start_experiment(
            StartExperiment(
                project_id=original["project_id"],
                contract_id=original["contract_id"],
                target_config_id=original["target_config_id"],
            ),
            "other-proposal-scope",
        )
        with pytest.raises(ServiceError, match="another experiment"):
            service.candidates.create(
                other.experiment_id,
                CreateMutation(plan=MutationPlan.model_validate(linked["plan_json"]), rng_seed=17),
                "foreign-proposal",
                source_plan_id=linked["id"],
                automated=True,
            )
