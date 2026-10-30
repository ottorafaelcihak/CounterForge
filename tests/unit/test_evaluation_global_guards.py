"""Global evaluation guards carry a durable, distinguishable stop reason."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from counterforge_contracts.versions import ErrorCode

from services.api.app import evaluation
from services.api.app.errors import ServiceError
from services.api.app.evaluation import (
    GLOBAL_EVALUATION_STOP_REASON,
    GlobalEvaluationExhausted,
    require_runtime,
    reserve_global,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


class _Budget:
    def __init__(self, value, *, usage_rows=()):
        from types import SimpleNamespace

        from sqlalchemy import Column, MetaData, String, Table

        metadata = MetaData()
        usage = Table(
            "usage_events",
            metadata,
            Column("experiment_id", String),
            Column("status", String),
        )
        experiments = Table("experiments", metadata, Column("id", String))
        self._value = value
        self._usage_rows = list(usage_rows)
        self.db = self
        self.t = SimpleNamespace(usage_events=usage, experiments=experiments)

    def _now(self):
        return NOW

    # reserve_global: advisory lock execute, then peer-id scalars, then usage.
    def execute(self, _query):
        budget = self

        class _Result:
            def mappings(self):
                return self

            def all(self):
                return budget._usage_rows

        return _Result()

    def scalars(self, _query):
        return iter([])


def _value(**overrides):
    value = dict(
        experiment_ids=["11111111-1111-1111-1111-111111111111"],
        baseline_groups=[],
        started_at=(NOW - timedelta(seconds=10)).isoformat(),
        max_runtime_seconds=3600,
        max_api_calls=100,
        max_usd="10.00",
        authorization_expires_at=None,
        authorization_sha256=None,
        run_sha256="a" * 64,
    )
    value.update(overrides)
    return value


def _patch(monkeypatch, value):
    monkeypatch.setattr(evaluation, "envelope", lambda _e: value)
    monkeypatch.setattr(evaluation, "complete_membership", lambda _b, _e: True)
    monkeypatch.setattr(evaluation, "_scope_started_at", lambda _b, _v: value["started_at"])
    monkeypatch.setattr(evaluation, "_auth_peer_ids", lambda _b, _v: None)


def test_global_runtime_expiry_is_typed_with_durable_reason(monkeypatch):
    value = _value(started_at=(NOW - timedelta(seconds=7200)).isoformat())
    _patch(monkeypatch, value)
    with pytest.raises(GlobalEvaluationExhausted) as caught:
        require_runtime(_Budget(value), object())
    assert caught.value.stop_reason == GLOBAL_EVALUATION_STOP_REASON
    assert caught.value.code.value == "BUDGET_EXCEEDED"


def test_authorization_expiry_after_review_wait_is_global(monkeypatch):
    # Wall clock includes the human-review wait: started long ago, and the
    # authorization itself has now expired.
    value = _value(authorization_expires_at=(NOW - timedelta(seconds=1)).isoformat())
    _patch(monkeypatch, value)
    with pytest.raises(GlobalEvaluationExhausted) as caught:
        require_runtime(_Budget(value), object())
    assert caught.value.stop_reason == GLOBAL_EVALUATION_STOP_REASON


def test_global_dollar_envelope_exhaustion_is_global(monkeypatch):
    value = _value()
    _patch(monkeypatch, value)
    rows = [
        dict(
            request_count=99,
            status="SETTLED",
            cost_kind="ACTUAL",
            actual_usd=Decimal("9.99"),
            accounted_estimate_usd=Decimal("0"),
            estimated_max_usd=Decimal("0"),
        )
    ]
    with pytest.raises(GlobalEvaluationExhausted) as caught:
        reserve_global(_Budget(value, usage_rows=rows), object(), Decimal("1.00"), 1)
    assert caught.value.stop_reason == GLOBAL_EVALUATION_STOP_REASON


def test_within_global_envelope_does_not_raise(monkeypatch):
    value = _value()
    _patch(monkeypatch, value)
    require_runtime(_Budget(value), object())
    reserve_global(_Budget(value), object(), Decimal("0.01"), 1)


def test_local_budget_error_has_no_global_reason():
    err = ServiceError(ErrorCode.BUDGET_EXCEEDED, "local cap")
    assert not isinstance(err, GlobalEvaluationExhausted)
    assert getattr(err, "stop_reason", None) != GLOBAL_EVALUATION_STOP_REASON
