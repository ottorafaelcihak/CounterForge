"""Sanitized, typed finding refinement and version comparison evidence."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import ContractModel, FiniteNumber
from .requests import InferenceOptions
from .versions import TargetOutcome


class MinimizationProbe(ContractModel):
    mutation_id: UUID | None = None
    finding_id: UUID | None = None
    strength: FiniteNumber = Field(ge=0, le=1)
    outcome: str
    screening_outcome: TargetOutcome | None = None
    confirmation_outcomes: list[TargetOutcome] = Field(default_factory=list)


class MinimizationResult(ContractModel):
    source_finding_id: UUID
    source_mutation_id: UUID
    best_finding_id: UUID
    best_mutation_id: UUID
    probe_limit: int = Field(ge=1, le=8)
    probes_used: int = Field(ge=0, le=8)
    probes: list[MinimizationProbe] = Field(default_factory=list)
    stop_reason: str
    qualification: str = "Simplest tested stable variant; no claim of a global minimum. Each child requires independent human review and three fresh incorrect confirmations."
    usage: dict[str, FiniteNumber] = Field(default_factory=dict)


class RetestTarget(ContractModel):
    adapter_key: Literal["META_SPARK", "LOCAL_FIXTURE"]
    model_id: str
    configured_revision: str
    observed_revisions: list[str] = Field(default_factory=list)
    inference_options: InferenceOptions


class RetestComparison(ContractModel):
    export_id: UUID
    source_finding_id: UUID
    target_config_id: UUID
    target_a: RetestTarget
    target_b: RetestTarget
    bundle_sha256: str
    manifest_sha256: str
    input_sha256: str
    prompt_sha256: str
    expected_count: int = Field(ge=0)
    historical_screening_incorrect: int = 1
    historical_fresh_incorrect: int = 3
    historical_fresh_attempts: int = 3
    planned_attempts: Literal[3] = 3
    completed_attempts: int = Field(ge=0, le=3)
    correct: int = Field(ge=0, le=3)
    incorrect: int = Field(ge=0, le=3)
    unknown: int = Field(ge=0, le=3)
    errors: int = Field(ge=0, le=3)
    interpretable_attempts: int = Field(ge=0, le=3)
    classification: Literal["FIXED", "PERSISTENT", "INTERMITTENT", "INCONCLUSIVE"]
    observation_ids: list[UUID] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    qualification: str = "Coverage is completed/3; correctness uses interpretable attempts only. Configured revisions are owner labels, not proof of the actual served model version. Provider-managed behavior may change; LOCAL_FIXTURE is simulated."
