from datetime import UTC, datetime
from decimal import Decimal

from services.api.app.comparison import summarize_usage


def test_all_expenses_and_unknown_reservations_are_separate_not_silently_zeroed():
    def row(status, kind, actual="0", estimate="0", upper="1", dispatched=True):
        return {
            "status": status,
            "cost_kind": kind,
            "actual_usd": Decimal(actual),
            "accounted_estimate_usd": Decimal(estimate),
            "estimated_max_usd": Decimal(upper),
            "dispatched_at": datetime.now(UTC) if dispatched else None,
            "request_count": 1,
        }

    rows = [
        row("SETTLED", "ACTUAL", actual="0.01"),
        row("SETTLED", "ESTIMATED", estimate="0.02"),
        row("UNKNOWN_CHARGE", "UNKNOWN", upper="0.5"),
        row("RESERVED", None, upper="0.7", dispatched=False),
        row("VOID", "UNBILLED", dispatched=False),
    ]
    result = summarize_usage(rows)
    assert result["request_slots_total"] == 5 and result["dispatched_requests"] == 3
    assert Decimal(result["actual_usd"]) == Decimal("0.01")
    assert Decimal(result["estimated_usd"]) == Decimal("0.02")
    assert Decimal(result["unknown_usd"]) == Decimal("0.5")
    assert Decimal(result["reserved_usd"]) == Decimal("0.7")
