from typing import Literal, Self

from pydantic import Field, model_validator

from .base import ContractModel, FiniteNumber
from .versions import MutationFamily
from .vtc import VisualTestContract


class MutationPlan(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    hypothesis: str = Field(min_length=1, max_length=1000)
    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)
    prompt: str = Field(min_length=1, max_length=2000)
    target_region: Literal["background_only", "full_image"] = "background_only"
    requires_muse_image: bool
    estimated_cost_usd: FiniteNumber = Field(ge=0)

    @model_validator(mode="after")
    def bounded_target_region(self) -> Self:
        if self.target_region != "background_only" and (
            self.requires_muse_image or self.family != MutationFamily.BRIGHTNESS_SHIFT
        ):
            raise ValueError("generative and background-family edits must target background_only")
        return self

    def validate_for_contract(self, contract: VisualTestContract) -> Self:
        if self.family not in contract.allowed_mutations:
            raise ValueError("mutation family is not allowed by the contract")
        return self


class PlannerProposal(ContractModel):
    """Provider structured output, expanded by the server into a MutationPlan."""

    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)
    hypothesis: str = Field(min_length=1, max_length=1000)
    prompt: str = Field(min_length=1, max_length=2000)
