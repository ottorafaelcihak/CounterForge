from decimal import Decimal

import pytest
from sqlalchemy import select

from services.api.app.accounting import Budget
from services.api.app.errors import ServiceError
from tests.integrations import test_budget_reservations as previous

run = previous.run


async def test_semantic_input_changes_cannot_reuse_financial_operation_key(api, run):
    with api.factory() as uow:
        Budget(uow).reserve_operation(
            run, "semantic", "TARGET", Decimal("0.1"), 1, semantic_sha256="a" * 64
        )
        uow.commit()
    with pytest.raises(ServiceError) as error, api.factory() as uow:
        Budget(uow).reserve_operation(
            run, "semantic", "TARGET", Decimal("0.1"), 1, semantic_sha256="b" * 64
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


async def test_estimated_success_is_consumed_but_never_reported_as_actual(api, run):
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_operation(run, "estimated", "TARGET", Decimal("0.2"), 1)
        budget.begin_operation("estimated")
        budget.settle_estimate("estimated")
        budget.settle_estimate("estimated")
        uow.commit()
    with api.factory() as uow:
        usage = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert usage["cost_kind"] == "ESTIMATED" and usage["actual_usd"] is None
        assert usage["accounted_estimate_usd"] == Decimal("0.2")
    row = previous.counters(api, run)
    assert (
        row["spent_usd"] == Decimal("0.2")
        and row["reserved_usd"] == 0
        and row["calls_consumed"] == 1
    )
    metrics = (await api.client.get(f"/api/v1/experiments/{run}")).json()["metrics"]
    assert metrics["actual_provider_usage_usd"] == 0 and metrics["conservative_estimate_usd"] == 0.2


@pytest.mark.parametrize("sent,expected_calls", [(False, 0), (True, 1)])
async def test_proven_unbilled_claim_keeps_history_without_creating_candidates(
    api, run, sent, expected_calls
):
    with api.factory() as uow:
        budget = Budget(uow)
        budget.reserve_operation(run, "unbilled", "TARGET", Decimal("0.2"), 1, 1)
        budget.begin_operation("unbilled")
        budget.finish_unbilled(
            "unbilled",
            proof_code="UNBILLED_RATE_LIMIT" if sent else "CONNECT_FAILED",
            request_sent=sent,
        )
        uow.commit()
    row = previous.counters(api, run)
    assert (
        row["spent_usd"] == 0
        and row["reserved_usd"] == 0
        and row["calls_consumed"] == expected_calls
    )
    assert row["candidates_created"] == 0
    with api.factory() as uow:
        operation = uow.connection.execute(select(uow.tables.run_operations)).mappings().one()
        assert operation["status"] == "FAILED" and operation["started_at"] is not None
        usage = uow.connection.execute(select(uow.tables.usage_events)).mappings().one()
        assert usage["cost_kind"] == "UNBILLED" and usage["actual_usd"] == 0
