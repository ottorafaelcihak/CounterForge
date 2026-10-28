from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from services.api.app.accounting import Budget, ProviderUsage
from services.api.app.errors import ServiceError
from tests.integrations import test_budget_reservations as budget_tests
from tests.integrations.test_budget_reservations import counters, reserve
from tests.integrations.test_contracts_and_jobs import setup_case

run = budget_tests.run


@pytest.fixture
async def jobs(api, vtc_payload):
    seed, _, _ = await setup_case(api, vtc_payload)
    result = []
    for _ in range(2):
        response = await api.client.post(
            f"/api/v1/seeds/{seed['id']}/segment",
            json={
                "concept": "red container",
                "limits": {"max_estimated_usd": 1, "max_calls": 3, "max_runtime_seconds": 60},
            },
        )
        assert response.status_code == 202
        result.append(UUID(response.json()["job_id"]))
    with api.factory() as uow:
        Budget(uow).configure_deployment_allowance(
            max_usd=Decimal("1"), max_calls=6, expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        uow.commit()
    return result


async def test_job_intent_requires_a_single_dispatch_claim_and_only_unsent_intent_is_voidable(
    api, jobs
):
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_job_operation(jobs[0], "job-intent", "SAM", Decimal("0.2"))
        usage = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert usage["dispatched_at"] is None
        uow.commit()
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).settle_operation("job-intent", ProviderUsage(Decimal("0.1")))

    def claim(_):
        with api.factory() as uow:
            value = Budget(uow).begin_operation("job-intent")
            uow.commit()
            return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).void_operation("job-intent")
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_job_operation(jobs[1], "unsent-job", "SAM", Decimal("0.3"))
        assert budget.void_operation("unsent-job").released_usd == Decimal("0.3")
        allowance = budget.deployment_allowance()
        assert allowance.consumed_usd == Decimal("0.2") and allowance.consumed_calls == 1
        uow.commit()


@pytest.mark.parametrize("expired", ["runtime", "allowance", "state"])
async def test_late_job_dispatch_rechecks_runtime_allowance_and_state(api, jobs, expired):
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_job_operation(jobs[0], "late-job", "SAM", Decimal("0.2"))
        if expired == "allowance":
            uow.connection.execute(
                uow.tables.deployment_allowance.update().values(
                    expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
        else:
            values = (
                {"created_at": datetime.now(UTC) - timedelta(seconds=120)}
                if expired == "runtime"
                else {"state": "FAILED"}
            )
            uow.connection.execute(
                uow.tables.background_jobs.update()
                .where(uow.tables.background_jobs.c.id == jobs[0])
                .values(**values)
            )
        uow.commit()
    with pytest.raises(ServiceError) as error, api.factory() as uow:
        Budget(uow).begin_operation("late-job")
    assert error.value.code == ("INVALID_STATE" if expired == "state" else "BUDGET_EXCEEDED")


async def test_above_bound_job_charge_increases_shared_consumption_once(api, jobs):
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_job_operation(jobs[0], "job-overrun", "SAM", Decimal("0.2"))
        assert budget.begin_operation("job-overrun")
        for _ in range(2):
            budget.settle_operation("job-overrun", ProviderUsage(Decimal("0.8")))
        assert budget.deployment_allowance().consumed_usd == Decimal("0.8")
        uow.commit()
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).reserve_job_operation(jobs[1], "other-job", "SAM", Decimal("0.3"))


async def test_experiment_jobs_cannot_bypass_experiment_limits_with_the_job_allowance(
    api, jobs, run
):
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.background_jobs.update()
            .where(uow.tables.background_jobs.c.id == jobs[0])
            .values(experiment_id=run)
        )
        uow.commit()
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).reserve_job_operation(jobs[0], "bypass", "TARGET", Decimal("0.1"))


@pytest.mark.parametrize(
    "actual,state,reason",
    [
        ("0.4", "PAUSED", "PROVIDER_COST_ABOVE_RESERVATION"),
        ("1.1", "BUDGET_EXHAUSTED", "ESTIMATED_SPEND_CAP_REACHED"),
    ],
)
async def test_inflight_overrun_stops_an_experiment_even_after_pause_requested(
    api, run, actual, state, reason
):
    reserve(api, run, "pause-overrun", usd="0.2", calls=1)
    with api.factory() as uow:
        budget = Budget(uow)
        budget.begin_operation("pause-overrun")
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == run)
            .values(state="PAUSE_REQUESTED")
        )
        budget.settle_operation("pause-overrun", ProviderUsage(Decimal(actual)))
        uow.commit()
    row = counters(api, run)
    assert row["state"] == state and row["stop_reason"] == reason


async def test_provably_unsent_intent_cannot_become_an_unknown_charge(api, run):
    reserve(api, run, "unsent")
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).mark_unknown_charge("unsent", "timeout")
    assert counters(api, run)["spent_usd"] == 0
    with api.factory() as uow:
        assert Budget(uow).void_operation("unsent").recorded
        uow.commit()


async def test_surplus_reports_only_unused_reservation_and_changed_attribution_conflicts(api, run):
    reserve(api, run, "surplus", usd="0.8")
    with api.factory() as uow:
        budget = Budget(uow)
        budget.begin_operation("surplus")
        result = budget.settle_operation(
            "surplus", ProviderUsage(Decimal("0.2"), tokens_in=10, provider_request_id="req-1")
        )
        assert result.released_usd == Decimal("0.6")
        uow.commit()
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).settle_operation(
            "surplus", ProviderUsage(Decimal("0.2"), tokens_in=20, provider_request_id="req-2")
        )
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).reserve_operation(
            run, "surplus", "TARGET", Decimal("0.8"), provider="DIFFERENT"
        )


async def test_same_global_key_across_scopes_returns_idempotency_conflict(api, jobs, run):
    def create(scope):
        try:
            with api.factory() as uow:
                budget = Budget(uow)
                if scope == "experiment":
                    result = budget.reserve_operation(run, "shared-key", "TARGET", Decimal("0.1"))
                else:
                    result = budget.reserve_job_operation(
                        jobs[0], "shared-key", "SAM", Decimal("0.1")
                    )
                uow.commit()
                return result.created
        except ServiceError as error:
            assert error.code == "IDEMPOTENCY_CONFLICT"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, ["experiment", "job"])) == [False, True]
