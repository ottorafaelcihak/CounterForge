from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select

from services.api.app.errors import ServiceError
from services.worker.app.budget import Budget, ProviderUsage
from tests.integrations.test_contracts_and_jobs import contract, setup_case, target


@pytest.fixture
async def run(api, vtc_payload):
    vtc_payload["limits"] = {
        "max_candidates": 4,
        "max_target_calls": 3,
        "max_estimated_usd": 1.0,
        "max_runtime_seconds": 60,
    }
    _, _, vtc = await contract(api, vtc_payload)
    config = await target(api)
    response = await api.client.post(
        "/api/v1/experiments",
        json={
            "project_id": api.project_id,
            "contract_id": vtc["id"],
            "target_config_id": config["id"],
            "limits": vtc["contract"]["limits"],
        },
        headers={"Idempotency-Key": "budget-test"},
    )
    assert response.status_code == 202, response.text
    return UUID(response.json()["experiment_id"])


def reserve(api, experiment_id, key, *, usd="0.1", calls=0, candidates=0, stage="TARGET"):
    with api.factory() as uow:
        result = Budget(uow).reserve_operation(
            experiment_id, key, stage, Decimal(usd), calls, candidates
        )
        uow.commit()
        return result


def counters(api, experiment_id):
    with api.factory() as uow:
        return dict(
            uow.connection.execute(
                select(uow.tables.experiments).where(uow.tables.experiments.c.id == experiment_id)
            )
            .mappings()
            .one()
        )


@pytest.mark.parametrize("usd,calls,candidates", [("0.6", 0, 0), ("0.1", 2, 0), ("0.1", 0, 3)])
async def test_competing_reservations_cannot_oversubscribe_any_cap(
    api, run, usd, calls, candidates
):
    def attempt(key):
        try:
            return reserve(api, run, key, usd=usd, calls=calls, candidates=candidates)
        except ServiceError as error:
            assert error.code == "BUDGET_EXCEEDED"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["one", "two"]))
    assert sum(result is not None for result in results) == 1
    with api.factory() as uow:
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.run_operations)) == 1
        )
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events)) == 1
    row = counters(api, run)
    assert row["reserved_usd"] + row["spent_usd"] <= row["max_estimated_usd"]


async def test_duplicate_operation_and_dispatch_claims_never_duplicate_work(api, run):
    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(
            pool.map(
                lambda _: reserve(api, run, "same", usd="0.3", calls=1, candidates=1), range(2)
            )
        )
    assert sorted(result.created for result in reservations) == [False, True]
    assert counters(api, run)["reserved_usd"] == Decimal("0.3")

    def claim(_):
        with api.factory() as uow:
            result = Budget(uow).begin_operation("same")
            uow.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
    with pytest.raises(ServiceError):
        reserve(api, run, "same", usd="0.4", calls=1, candidates=1)


async def test_settlement_releases_surplus_and_is_idempotent(api, run):
    reserve(api, run, "settle", usd="0.8", calls=2, candidates=1)
    for _ in range(2):
        with api.factory() as uow:
            budget = Budget(uow)
            budget.begin_operation("settle")
            budget.settle_operation("settle", ProviderUsage(actual_usd=Decimal("0.2")))
            uow.commit()
    row = counters(api, run)
    assert row["reserved_usd"] == 0 and row["spent_usd"] == Decimal("0.2")
    assert row["calls_consumed"] == 2 and row["candidates_created"] == 1
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).settle_operation("settle", ProviderUsage(actual_usd=Decimal("0.3")))


async def test_unknown_charge_is_consumed_once_and_cannot_be_automatically_refunded(api, run):
    reserve(api, run, "ambiguous", usd="0.7", calls=2)
    with api.factory() as uow:
        budget = Budget(uow)
        assert budget.begin_operation("ambiguous")
        budget.mark_unknown_charge("ambiguous", "post-send timeout")
        budget.mark_unknown_charge("ambiguous", "post-send timeout")
        uow.commit()
    row = counters(api, run)
    assert row["spent_usd"] == Decimal("0.7") and row["reserved_usd"] == 0
    assert row["calls_consumed"] == 2 and row["state"] == "PAUSED"
    for method in ("settle", "void"):
        with pytest.raises(ServiceError), api.factory() as uow:
            budget = Budget(uow)
            if method == "settle":
                budget.settle_operation("ambiguous", ProviderUsage(actual_usd=Decimal("0")))
            else:
                budget.void_operation("ambiguous")
    assert counters(api, run)["spent_usd"] == Decimal("0.7")
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == run)
            .values(state="READY")
        )
        uow.commit()
    with pytest.raises(ServiceError):
        reserve(api, run, "too-much", usd="0.4")


async def test_expiry_and_stop_state_prevent_new_reservations_and_late_dispatch(api, run):
    reserve(api, run, "waiting", usd="0.2")
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == run)
            .values(started_at=datetime.now(UTC) - timedelta(seconds=100))
        )
        uow.commit()
    with pytest.raises(ServiceError):
        reserve(api, run, "expired")
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).begin_operation("waiting")
    with api.factory() as uow:
        uow.connection.execute(
            uow.tables.experiments.update()
            .where(uow.tables.experiments.c.id == run)
            .values(started_at=func.now(), state="CANCEL_REQUESTED")
        )
        uow.commit()
    with pytest.raises(ServiceError):
        reserve(api, run, "cancelled")


async def test_only_undispatched_intent_can_be_voided(api, run):
    reserve(api, run, "not-sent", usd="0.4", calls=2, candidates=3)
    with api.factory() as uow:
        Budget(uow).void_operation("not-sent")
        uow.commit()
    assert counters(api, run)["reserved_usd"] == 0
    reserve(api, run, "sent", usd="0.4")
    with api.factory() as uow:
        Budget(uow).begin_operation("sent")
        uow.commit()
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).void_operation("sent")


async def test_preexperiment_dispatch_requires_job_limits_and_shared_allowance(api, vtc_payload):
    seed, _, _ = await setup_case(api, vtc_payload)
    path = f"/api/v1/seeds/{seed['id']}/segment"
    missing = await api.client.post(path, json={"concept": "red container"})
    missing_id = UUID(missing.json()["job_id"])
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).reserve_job_operation(missing_id, "no-limits", "SAM", Decimal("0.1"), calls=1)
    limits = {"max_estimated_usd": 1.0, "max_calls": 2, "max_runtime_seconds": 60}
    jobs = []
    for _ in range(2):
        response = await api.client.post(path, json={"concept": "red container", "limits": limits})
        assert response.status_code == 202, response.text
        jobs.append(UUID(response.json()["job_id"]))
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).reserve_job_operation(jobs[0], "no-allowance", "SAM", Decimal("0.4"), calls=1)
    with api.factory() as uow:
        Budget(uow).configure_deployment_allowance(
            max_usd=Decimal("0.5"), max_calls=1, expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        uow.commit()

    def attempt(pair):
        job_id, key = pair
        try:
            with api.factory() as uow:
                result = Budget(uow).reserve_job_operation(
                    job_id, key, "SAM", Decimal("0.4"), calls=1
                )
                uow.commit()
                return result
        except ServiceError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, zip(jobs, ("job-one", "job-two"), strict=True)))
    assert sum(result is not None for result in results) == 1
    with api.factory() as uow:
        usage = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert usage["experiment_id"] is None and usage["background_job_id"] in jobs
        assert usage["estimated_max_usd"] == Decimal("0.4")
        assert uow.connection.execute(select(uow.tables.run_operations)).all() == []


async def test_cost_above_the_reservation_is_recorded_and_stops_further_work(api, run):
    reserve(api, run, "underestimated", usd="0.2", calls=1)
    with api.factory() as uow:
        budget = Budget(uow)
        assert budget.begin_operation("underestimated")
        settlement = budget.settle_operation(
            "underestimated", ProviderUsage(actual_usd=Decimal("0.35"))
        )
        uow.commit()
    assert settlement.above_reservation is True
    row = counters(api, run)
    assert row["spent_usd"] == Decimal("0.35") and row["reserved_usd"] == 0
    assert row["calls_consumed"] == 1 and row["state"] == "PAUSED"
    with pytest.raises(ServiceError):
        reserve(api, run, "after-stop", usd="0.1")


async def test_float_amounts_are_rejected_before_anything_is_reserved(api, run):
    with pytest.raises(ServiceError) as error, api.factory() as uow:
        Budget(uow).reserve_operation(run, "float-money", "TARGET", 0.1, 1, 0)
    assert error.value.code == "INVALID_INPUT"
    with api.factory() as uow:
        assert uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events)) == 0
    assert counters(api, run)["reserved_usd"] == 0


async def test_job_calls_are_all_counted_and_allowance_updates_keep_consumed_totals(
    api, vtc_payload
):
    seed, _, _ = await setup_case(api, vtc_payload)
    limits = {"max_estimated_usd": 1.0, "max_calls": 1, "max_runtime_seconds": 60}
    response = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/segment", json={"concept": "red container", "limits": limits}
    )
    assert response.status_code == 202, response.text
    job_id = UUID(response.json()["job_id"])
    with api.factory() as uow:
        Budget(uow).configure_deployment_allowance(
            max_usd=Decimal("5"), max_calls=10, expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        uow.commit()
    with api.factory() as uow:
        first = Budget(uow).reserve_job_operation(job_id, "job-call-one", "SAM", Decimal("0.2"))
        uow.commit()
    assert first.created is True
    # The per-job call limit binds even while the shared allowance still has room.
    with pytest.raises(ServiceError), api.factory() as uow:
        Budget(uow).reserve_job_operation(job_id, "job-call-two", "SAM", Decimal("0.2"))
    with api.factory() as uow:
        budget = Budget(uow)
        replay = budget.reserve_job_operation(job_id, "job-call-one", "SAM", Decimal("0.2"))
        updated = budget.configure_deployment_allowance(
            max_usd=Decimal("9"), max_calls=20, expires_at=datetime.now(UTC) + timedelta(hours=2)
        )
        uow.commit()
    assert replay.created is False
    assert updated.consumed_usd == Decimal("0.2") and updated.consumed_calls == 1
