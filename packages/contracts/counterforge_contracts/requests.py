from typing import Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from .base import ContractModel, FiniteNumber
from .plans import MutationPlan
from .versions import ValidityVerdict
from .vtc import Limits


class JobLimits(ContractModel):
    """Explicit owner-authorized ceiling for one pre-experiment job.

    Every field is required: a job without stated finite limits may be queued,
    but it must never be dispatched to a paid provider.
    """

    max_estimated_usd: FiniteNumber = Field(gt=0, le=150)
    max_calls: StrictInt = Field(ge=1, le=100)
    max_runtime_seconds: StrictInt = Field(ge=60, le=7200)


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
    limits: JobLimits | None = None


class TargetCheckRequest(ContractModel):
    seed_id: UUID | None = None
    limits: JobLimits | None = None


class ReviewDecision(ContractModel):
    decision: ValidityVerdict
    reason_code: str = Field(min_length=1, max_length=100)
    notes: str = Field(min_length=1, max_length=2000)


class StartExperiment(ContractModel):
    project_id: UUID
    contract_id: UUID
    target_config_id: UUID
    mode: Literal["ADAPTIVE", "RANDOM_BASELINE"] = "ADAPTIVE"
    search_policy: Literal["ONE_SHOT", "FEEDBACK_V1", "RANDOM_V1"] = "ONE_SHOT"
    random_seed: StrictInt | None = Field(default=None, ge=0, le=2**63 - 1)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def feedback_requires_adaptive(self) -> Self:
        if self.search_policy == "FEEDBACK_V1" and self.mode != "ADAPTIVE":
            raise ValueError("FEEDBACK_V1 requires ADAPTIVE mode")
        if self.search_policy == "RANDOM_V1" and self.mode != "RANDOM_BASELINE":
            raise ValueError("RANDOM_V1 requires RANDOM_BASELINE mode")
        return self


class ConfirmRequest(ContractModel):
    attempts: Literal[3] = 3


class RetestRequest(ContractModel):
    target_config_id: UUID
    limits: JobLimits


class MinimizeRequest(ContractModel):
    probe_limit: StrictInt = Field(default=8, ge=1, le=8)


class AcknowledgeMaskMismatch(ContractModel):
    detected_count: StrictInt | None = Field(default=None, ge=0, le=1000)
    human_count: StrictInt = Field(ge=1, le=16)
    notes: str = Field(min_length=10, max_length=2000)


class CreateMutation(ContractModel):
    plan: MutationPlan
    rng_seed: StrictInt = Field(ge=-(2**63), le=2**63 - 1)
    background_job_id: UUID | None = None


class GroundTruthAuditRequest(ContractModel):
    resolved_check_codes: list[Literal["HUMAN_SEMANTICS", "HUMAN_VISIBILITY"]] = Field(
        min_length=1, max_length=2
    )
    note: str = Field(min_length=1, max_length=2000)


class MutationReviewRequest(ContractModel):
    prior_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_hashes: dict[str, str]
    semantic: Literal["consistent", "additional_object", "removed_object", "ambiguous"]
    visibility: Literal["clear", "ambiguous", "destroyed"]
    notes: str = Field(min_length=1, max_length=2000)
    ground_truth_audit: GroundTruthAuditRequest | None = None


class BackgroundRequest(ContractModel):
    plan: MutationPlan
    limits: JobLimits


class CandidateSegmentationRequest(ContractModel):
    limits: JobLimits


class DemoSnapshotPrepareRequest(ContractModel):
    """Optional preparation choices; preparation itself never publishes."""

    include_seed_image: bool = False


class DemoPublicationRequest(ContractModel):
    """Explicit operator publication intent; preparation alone never publishes.

    Republication rights are distinct from the private seed-use license
    acknowledgement: the operator must confirm them for the exact prepared
    manifest hash being published.
    """

    publication_intent: str = Field(min_length=10, max_length=500)
    confirm_publication: Literal[True]
    redistribution_rights_acknowledged: Literal[True]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
