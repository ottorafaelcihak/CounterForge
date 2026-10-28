from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import ContractModel, FiniteNumber
from .requests import InferenceOptions
from .versions import (
    ErrorCode,
    ExperimentState,
    FindingStatus,
    JobState,
    MutationFamily,
    MutationState,
    ValidityVerdict,
)
from .vtc import ExpectedCount, Limits, VisualTestContract

LocalLink = str


class ErrorDetails(ContractModel):
    remaining_estimated_usd: FiniteNumber | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0)


class ErrorBody(ContractModel):
    code: ErrorCode
    message: str
    correlation_id: str
    retryable: bool = False
    details: ErrorDetails = Field(default_factory=ErrorDetails)


class ErrorEnvelope(ContractModel):
    error: ErrorBody


class Health(ContractModel):
    status: Literal["alive"] = "alive"
    api_version: Literal[1] = 1
    schema_version: Literal["1.0.0"] = "1.0.0"


class Page[T](ContractModel):
    items: list[T]
    next_cursor: str | None = None


class JobLinks(ContractModel):
    self: LocalLink = Field(pattern=r"^/api/v1/jobs/[0-9a-f-]+$")


class ExperimentLinks(ContractModel):
    self: LocalLink = Field(pattern=r"^/api/v1/experiments/[0-9a-f-]+$")
    events: LocalLink = Field(pattern=r"^/api/v1/experiments/[0-9a-f-]+/events$")


class JobRef(ContractModel):
    job_id: UUID
    state: JobState
    links: JobLinks


class ExperimentRef(ContractModel):
    experiment_id: UUID
    state: ExperimentState
    links: ExperimentLinks


class Project(ContractModel):
    id: UUID
    name: str
    description: str
    task_type: Literal["object_counting"] = "object_counting"
    created_at: datetime


class SeedProvenance(ContractModel):
    original_asset_id: UUID
    normalized_asset_id: UUID
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pixel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_policy: str
    icc_policy: Literal["discard_no_color_conversion"] = "discard_no_color_conversion"
    rights_acknowledged: Literal[True] = True


class Seed(ContractModel):
    id: UUID
    project_id: UUID
    image_asset_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question: str
    expected: ExpectedCount
    label_provenance: Literal["HUMAN", "LICENSED_DATASET"]
    case_key: str
    provenance: SeedProvenance


class Mask(ContractModel):
    id: UUID
    seed_id: UUID
    mask_asset_id: UUID
    object_label: str
    object_ordinal: int = Field(ge=0)
    reviewed: bool
    reviewer: str | None = None
    reviewed_at: datetime | None = None


class Contract(ContractModel):
    id: UUID
    seed_id: UUID
    version: int = Field(ge=1)
    contract: VisualTestContract
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_at: datetime | None = None


class Target(ContractModel):
    id: UUID
    project_id: UUID
    adapter_key: Literal["META_SPARK", "LOCAL_FIXTURE"]
    model_id: str
    model_revision: str
    inference_options: InferenceOptions
    enabled: bool


class CheckEvidence(ContractModel):
    code: str
    status: Literal["PASS", "FAIL", "UNKNOWN", "REQUIRED"]
    value: FiniteNumber | None = None
    observed: int | None = None
    expected: int | None = None


class ValidationReport(ContractModel):
    verdict: ValidityVerdict
    verifier_version: str
    checks: list[CheckEvidence]
    confidence_note: str
    evidence_asset_ids: list[UUID]


class Metrics(ContractModel):
    generated_candidates: int = Field(ge=0)
    valid_candidates: int = Field(ge=0)
    invalid_candidates: int = Field(ge=0)
    review_required_candidates: int = Field(ge=0)
    valid_screening_calls: int = Field(ge=0)
    total_target_calls: int = Field(ge=0)
    stable_findings: int = Field(ge=0)
    actual_provider_usage_usd: FiniteNumber = Field(ge=0)
    conservative_estimate_usd: FiniteNumber = Field(ge=0)
    unresolved_charges_usd: FiniteNumber = Field(ge=0)


class ExperimentView(ContractModel):
    id: UUID
    project_id: UUID
    contract_id: UUID
    target_config_id: UUID
    state: ExperimentState
    limits: Limits
    metrics: Metrics
    stop_reason: str | None = None


class Mutation(ContractModel):
    id: UUID
    experiment_id: UUID
    parent_mutation_id: UUID | None = None
    image_asset_id: UUID | None = None
    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)
    state: MutationState
    validation: ValidationReport | None = None


class ModelIdentity(ContractModel):
    adapter: Literal["META_SPARK", "LOCAL_FIXTURE"]
    model_id: str
    revision: str


class FindingTruth(ContractModel):
    type: Literal["integer"] = "integer"
    value: int = Field(strict=True, ge=0, le=1000)
    verified_by: Literal["human"] = "human"


class FindingMutation(ContractModel):
    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)


class FindingValidation(ContractModel):
    verdict: ValidityVerdict
    verifier_version: str
    evidence_ids: list[UUID]


class Confirmation(ContractModel):
    incorrect: int = Field(ge=0)
    attempts: int = Field(ge=0)
    raw_observation_ids: list[UUID]


class FindingAssets(ContractModel):
    original_image_id: UUID
    mutated_image_id: UUID


class Finding(ContractModel):
    id: UUID
    status: FindingStatus
    ground_truth: FindingTruth
    model: ModelIdentity
    mutation: FindingMutation
    validation: FindingValidation
    confirmation: Confirmation
    assets: FindingAssets
    qualification: str


class ExportLinks(ContractModel):
    self: LocalLink = Field(pattern=r"^/api/v1/exports/[0-9a-f-]+$")
    download: LocalLink | None = Field(
        default=None, pattern=r"^/api/v1/exports/[0-9a-f-]+/download$"
    )


class ExportRef(ContractModel):
    export_id: UUID
    state: Literal["QUEUED", "RUNNING", "READY", "FAILED", "REVOKED"]
    links: ExportLinks


class DemoManifest(ContractModel):
    recorded: Literal[True] = True
    provenance: Literal["REAL_PROVIDER", "LOCAL_FIXTURE"]
    experiment: ExperimentView
    findings: list[Finding]
    public_asset_ids: list[UUID]
    limitations: list[str]
