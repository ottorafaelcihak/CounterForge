from typing import Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from .base import ContractModel, FiniteNumber
from .versions import MutationFamily


class ExpectedCount(ContractModel):
    kind: Literal["integer"] = "integer"
    value: StrictInt = Field(ge=0, le=1000)
    verification: Literal["human"] = "human"
    reviewer: str = Field(min_length=1, max_length=100)


class ProtectedObjects(ContractModel):
    label: str = Field(min_length=2, max_length=100)
    expected_instances: StrictInt = Field(ge=1, le=16)
    mask_asset_ids: list[UUID] = Field(min_length=1, max_length=16)
    min_visible_fraction: FiniteNumber = Field(ge=0.8, le=1)

    @model_validator(mode="after")
    def distinct_instance_masks(self) -> Self:
        if len(set(self.mask_asset_ids)) != len(self.mask_asset_ids):
            raise ValueError("mask references must be distinct")
        if len(self.mask_asset_ids) != self.expected_instances:
            raise ValueError("each protected instance requires one mask reference")
        return self


class ValidationPolicy(ContractModel):
    uncertain: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    require_mask_review: Literal[True] = True


class ReproductionPolicy(ContractModel):
    attempts: StrictInt = Field(default=3, ge=1, le=5)
    required_failures: StrictInt = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def achievable_failure_threshold(self) -> Self:
        if self.required_failures > self.attempts:
            raise ValueError("required_failures must not exceed attempts")
        return self


class Limits(ContractModel):
    max_candidates: StrictInt = Field(default=20, ge=1, le=100)
    max_target_calls: StrictInt = Field(default=60, ge=3, le=500)
    max_estimated_usd: FiniteNumber = Field(default=10.0, gt=0, le=150)
    max_runtime_seconds: StrictInt = Field(default=1200, ge=60, le=7200)


class VisualTestContract(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    case_id: str = Field(min_length=3, max_length=120)
    task_type: Literal["object_counting"] = "object_counting"
    prompt: str = Field(min_length=10, max_length=300)
    expected: ExpectedCount
    protected: ProtectedObjects
    allowed_mutations: list[MutationFamily] = Field(min_length=1)
    forbidden_mutations: list[
        Literal["object_addition", "object_removal", "protected_region_repaint"]
    ] = Field(min_length=1)
    validation_policy: ValidationPolicy
    reproduction: ReproductionPolicy
    limits: Limits

    @model_validator(mode="after")
    def consistent_ground_truth(self) -> Self:
        if self.expected.value != self.protected.expected_instances:
            raise ValueError("expected count must match protected instances")
        if len(set(self.allowed_mutations)) != len(self.allowed_mutations):
            raise ValueError("allowed mutations must be distinct")
        if len(set(self.forbidden_mutations)) != len(self.forbidden_mutations):
            raise ValueError("forbidden mutations must be distinct")
        return self
