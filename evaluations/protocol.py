"""Immutable experiment declaration; live authority is separate from the protocol."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Field, StrictInt, model_validator

from .dataset import FrozenModel, Hash, ReviewTime, Text
from .integrity import seal, verify


def finite_money(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 0 or value > 150:
        raise ValueError("A finite dollar allowance between 0 and 150 is required")
    return value


Money = Annotated[Decimal, AfterValidator(finite_money)]
ARMS = ("random_det", "spark_det", "full_system")


class Protocol(FrozenModel):
    revision: Text
    dataset_sha256: Hash
    policy_revision: Literal["strict-binary-human-gates-1"]
    target_revision: Hash
    code_revision: Text
    random_seed: StrictInt = Field(ge=0, le=2**63 - 1)
    target_calls_per_arm: StrictInt = Field(ge=7, le=500)
    max_candidates_per_arm: StrictInt = Field(ge=1, le=100)
    max_api_calls: StrictInt = Field(ge=1, le=100000)
    max_usd: Money
    max_runtime_seconds: StrictInt = Field(ge=60, le=7200)
    arms: tuple[Literal["random_det", "spark_det", "full_system"], ...] = (
        "random_det",
        "spark_det",
        "full_system",
    )
    deterministic_families: tuple[Literal["brightness_shift"], ...] = ("brightness_shift",)
    full_families: tuple[
        Literal["brightness_shift", "background_replacement", "background_clutter"], ...
    ] = ("brightness_shift", "background_replacement", "background_clutter")
    strength_range: tuple[Literal[0], Literal[1]] = (0, 1)
    baseline_calls: Literal[3] = 3
    fresh_confirmation_calls: Literal[3] = 3
    # FEEDBACK_V1 (Phase 07 adaptive feedback) is the proposed benchmark
    # policy for ADAPTIVE arms. ONE_SHOT remains an explicitly labeled
    # option; its outcomes are not evidence about feedback-policy lift.
    # Under FEEDBACK_V1 the adapter maps the random baseline to the
    # matched seeded RANDOM_V1 loop (same caps, no planner or learned
    # preference); under ONE_SHOT every arm is one-shot. The declaration
    # records the one benchmark policy so both deterministic arms are
    # created, capped and reported under the same protocol revision.
    primary_policy: Literal["ONE_SHOT", "FEEDBACK_V1"] = "FEEDBACK_V1"
    stop_rule: Literal["worker_caps_or_review"] = "worker_caps_or_review"
    heldout_use: Literal["reporting_only"] = "reporting_only"
    order_rule: Literal["reverse_on_odd_seed"] = "reverse_on_odd_seed"
    seam_control: Literal["NOT_RUN"] = "NOT_RUN"
    sam_ablation: Literal["NOT_RUN"] = "NOT_RUN"
    sha256: Hash

    @model_validator(mode="after")
    def fixed_arms(self) -> Self:
        if self.arms != ARMS:
            raise ValueError("All three predeclared arms are required")
        return self

    def order(self, ordinal: int) -> tuple[str, ...]:
        return tuple(reversed(self.arms)) if ordinal % 2 else self.arms


def freeze_protocol(value: dict[str, Any]) -> Protocol:
    model = Protocol.model_validate(seal(value))
    return Protocol.model_validate(seal(model.model_dump(mode="json")))


def validate_protocol(model: Protocol) -> None:
    verify(model.model_dump(mode="json"))


class LiveAuthorization(FrozenModel):
    protocol_sha256: Hash
    dataset_sha256: Hash
    authorized_by: Text
    authorized_at: ReviewTime
    expires_at: datetime
    max_api_calls: StrictInt = Field(ge=1)
    max_usd: Money
    max_runtime_seconds: StrictInt = Field(ge=60, le=7200)
    paid_provider_calls_authorized: Literal[True]
    sha256: Hash

    def check(self, protocol: Protocol, now: datetime) -> None:
        verify(self.model_dump(mode="json"))
        if (
            self.protocol_sha256 != protocol.sha256
            or self.dataset_sha256 != protocol.dataset_sha256
        ):
            raise ValueError("Authorization does not bind the frozen protocol/dataset")
        if self.expires_at.tzinfo is None or not self.authorized_at <= now < self.expires_at:
            raise ValueError("Live authorization is expired or not yet valid")
        if (
            protocol.max_api_calls > self.max_api_calls
            or protocol.max_usd > self.max_usd
            or protocol.max_runtime_seconds > self.max_runtime_seconds
        ):
            raise ValueError("Protocol exceeds the explicit live authorization")
