"""Additive read models for server-retained search evidence."""

from typing import Literal

from pydantic import Field

from .base import ContractModel, FiniteNumber
from .versions import MutationFamily, TargetOutcome, ValidityVerdict


class HistoryOutcome(ContractModel):
    phase: Literal["SCREENING", "CONFIRMATION", "RETEST"]
    outcome: TargetOutcome


class PlannerHistory(ContractModel):
    hypothesis: str = Field(default="", max_length=500)
    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)
    recipe_parameters: dict[str, int | float | str | bool] = Field(default_factory=dict)
    validity: ValidityVerdict | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    target_outcomes: list[HistoryOutcome] = Field(default_factory=list, max_length=20)
    cost_usd: FiniteNumber = Field(default=0, ge=0)


class ProposalRanking(ContractModel):
    proposal_order: int = Field(ge=0)
    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)
    hypothesis: str = Field(max_length=500)
    estimated_cost_usd: FiniteNumber = Field(ge=0)
    p_valid: FiniteNumber = Field(ge=0, le=1)
    p_failure: FiniteNumber = Field(ge=0, le=1)
    family_trials: int = Field(ge=0)
    explore: FiniteNumber = Field(ge=0, le=1)
    score: FiniteNumber = Field(ge=0)
    rank: int | None = None
    selected: bool = False
    rejection_reason: str | None = None


class PlanningBatch(ContractModel):
    slot_index: int = Field(ge=0)
    policy_revision: str
    template_revision: str
    random_seed: int
    slot_strategy: str
    author: str
    fallback_reason: str | None = None
    exploration_reserved: int = Field(ge=0)
    exploration_used: int = Field(ge=0)
    remaining_budget: dict[str, FiniteNumber]
    cost_components: dict[str, FiniteNumber]
    history_snapshot: list[PlannerHistory] = Field(max_length=10)
    rankings: list[ProposalRanking]
