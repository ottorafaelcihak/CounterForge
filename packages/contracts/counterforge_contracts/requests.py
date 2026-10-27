from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import ContractModel, FiniteNumber
from .versions import ValidityVerdict
from .vtc import Limits


class CreateProject(ContractModel):
    name: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)


class InferenceOptions(ContractModel):
    temperature: FiniteNumber = Field(default=0.0, ge=0, le=2)
    top_p: FiniteNumber | None = Field(default=None, gt=0, le=1)
    max_output_tokens: int = Field(default=64, strict=True, ge=1, le=4096)


class CreateTargetConfig(ContractModel):
    adapter_key: Literal["META_SPARK", "LOCAL_FIXTURE"]
    model_id: str = Field(min_length=2, max_length=120)
    model_revision: str = Field(default="provider-managed", max_length=120)
    prompt_template: str = Field(min_length=10, max_length=300)
    inference_options: InferenceOptions = Field(default_factory=InferenceOptions)


class SegmentationRequest(ContractModel):
    concept: str = Field(min_length=2, max_length=100)


class ReviewDecision(ContractModel):
    decision: ValidityVerdict
    reason_code: str = Field(min_length=1, max_length=100)
    notes: str = Field(min_length=1, max_length=2000)


class StartExperiment(ContractModel):
    project_id: UUID
    contract_id: UUID
    target_config_id: UUID
    mode: Literal["ADAPTIVE", "RANDOM_BASELINE"] = "ADAPTIVE"
    limits: Limits = Field(default_factory=Limits)


class ConfirmRequest(ContractModel):
    attempts: Literal[3] = 3


class RetestRequest(ContractModel):
    target_config_id: UUID
    limits: Limits = Field(default_factory=Limits)
