from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, JsonValue

from .base import ContractModel, FiniteNumber
from .history import PlannerHistory, PlanningBatch
from .refinement import MinimizationResult, RetestComparison
from .requests import InferenceOptions
from .versions import (
    ErrorCode,
    ExperimentState,
    FindingStatus,
    JobState,
    MutationFamily,
    MutationState,
    TargetOutcome,
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
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    job_id: UUID
    state: JobState
    links: JobLinks

    error_code: str | None = None
    result: "JobResult | None" = None
    usage: "JobUsage" = Field(default_factory=lambda: JobUsage())


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


class MaskGeometry(ContractModel):
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    bbox_xyxy: tuple[int, int, int, int]
    foreground_pixels: int = Field(ge=1)
    binary: Literal[True] = True


class Mask(ContractModel):
    id: UUID
    seed_id: UUID
    mask_asset_id: UUID
    object_label: str
    object_ordinal: int = Field(ge=0)
    reviewed: bool
    reviewer: str | None = None
    reviewed_at: datetime | None = None

    revision: int = Field(default=1, ge=1)
    active: bool = True
    origin: Literal["SAM", "HUMAN", "FIXTURE"] = "SAM"
    geometry: MaskGeometry | None = None


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
    mandatory: bool = True
    detail: str = ""
    measurements: dict[str, int | str] = Field(default_factory=dict)


class ReviewAudit(ContractModel):
    actor: str
    note: str
    previous_verdict: ValidityVerdict
    previous_report_sha256: str
    reviewed_evidence_hashes: dict[str, str]
    semantic: str
    visibility: str
    ground_truth_audit_sha256: str | None = None


class ValidationReport(ContractModel):
    verdict: ValidityVerdict
    verifier_version: str
    checks: list[CheckEvidence]
    confidence_note: str
    evidence_asset_ids: list[UUID]
    policy_version: str = "legacy-unversioned"
    reason_codes: list[str] = Field(default_factory=list)
    evidence_hashes: dict[str, str] = Field(default_factory=dict)
    report_sha256: str | None = None
    review_audit: ReviewAudit | None = None


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
    mode: Literal["ADAPTIVE", "RANDOM_BASELINE"] = "ADAPTIVE"
    search_policy: Literal["ONE_SHOT", "FEEDBACK_V1", "RANDOM_V1"] = "ONE_SHOT"
    planner_history: list[PlannerHistory] = Field(default_factory=list)
    planning_batches: list[PlanningBatch] = Field(default_factory=list)
    synthetic: bool = False
    started_at: datetime | None = None
    elapsed_seconds: float = Field(default=0, ge=0)
    baseline_completed: int = Field(default=0, ge=0, le=3)
    spent_usd: FiniteNumber = Field(default=0, ge=0)
    reserved_usd: FiniteNumber = Field(default=0, ge=0)
    reserved_target_calls: int = Field(default=0, ge=0)
    resume_blocked_reason: str | None = None


class Mutation(ContractModel):
    id: UUID
    experiment_id: UUID
    parent_mutation_id: UUID | None = None
    minimization_job_id: UUID | None = None
    image_asset_id: UUID | None = None
    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)
    state: MutationState
    validation: ValidationReport | None = None
    validation_revision: int | None = None
    human_expected_count: int | None = None
    protected_label: str | None = None
    source_seed_id: UUID | None = None
    recipe: dict[str, Any] | None = None
    assets: dict[str, UUID] = Field(default_factory=dict)
    asset_hashes: dict[str, str] = Field(default_factory=dict)
    mask_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    synthetic: bool = False


class MutationValidationRevision(ContractModel):
    id: UUID
    mutation_id: UUID
    revision: int = Field(ge=1)
    previous_validation_id: UUID | None
    report: ValidationReport | None
    report_asset_id: UUID | None
    ground_truth_audit_asset_id: UUID | None = None
    ground_truth_audit: dict[str, Any] | None = None
    created_at: datetime


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


class AssetLink(ContractModel):
    asset_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_url: LocalLink = Field(pattern=r"^/api/v1/assets/[0-9a-f-]+/content$")


class FindingObservation(ContractModel):
    id: UUID
    phase: Literal["SEED_BASELINE", "SCREENING", "CONFIRMATION", "RETEST"]
    attempt_no: int = Field(ge=1, le=20)
    raw_text: str
    parsed_count: int | None
    outcome: TargetOutcome
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt: str
    sampling_options: dict[str, JsonValue]
    model_id: str
    model_revision: str
    provenance: Literal["LOCAL_FIXTURE", "REAL_PROVIDER"]
    latency_ms: int | None = None
    recorded_at: datetime


class FindingValidationRevision(ContractModel):
    revision: int
    report: ValidationReport
    created_at: datetime


class FindingEvidence(ContractModel):
    finding_id: UUID
    mutation_id: UUID
    experiment_id: UUID
    project_id: UUID
    question: str
    sampling_options: dict[str, JsonValue]
    provenance: Literal["LOCAL_FIXTURE", "REAL_PROVIDER"]
    validation: ValidationReport
    validation_history: list[FindingValidationRevision]
    assets: dict[str, AssetLink]
    observations: list[FindingObservation]
    usage: "FindingUsage"
    export_eligible: bool
    export_blocked_reasons: list[str]
    qualification: str


class Finding(ContractModel):
    id: UUID
    mutation_id: UUID | None = None
    status: FindingStatus
    ground_truth: FindingTruth
    model: ModelIdentity
    mutation: FindingMutation
    validation: FindingValidation
    confirmation: Confirmation
    assets: FindingAssets
    qualification: str
    evidence: FindingEvidence | None = None
    demo_approved: bool = False
    demo_approved_by: str | None = None
    demo_approved_at: datetime | None = None


class ExportLinks(ContractModel):
    self: LocalLink = Field(pattern=r"^/api/v1/exports/[0-9a-f-]+$")
    download: LocalLink | None = Field(
        default=None, pattern=r"^/api/v1/exports/[0-9a-f-]+/download$"
    )


class ExportRef(ContractModel):
    project_id: UUID | None = None
    export_id: UUID
    state: Literal["QUEUED", "RUNNING", "READY", "FAILED", "REVOKED"]
    links: ExportLinks
    finding_id: UUID | None = None
    job_id: UUID | None = None
    provenance: Literal["LOCAL_FIXTURE", "REAL_PROVIDER"] | None = None
    bundle_sha256: str | None = None
    manifest_sha256: str | None = None
    runner_version: str | None = None
    error_code: str | None = None


class DemoManifest(ContractModel):
    recorded: Literal[True] = True
    provenance: Literal["REAL_PROVIDER", "LOCAL_FIXTURE"]
    experiment: ExperimentView
    findings: list[Finding]
    public_asset_ids: list[UUID]
    limitations: list[str]


class MaskRevision(Mask):
    actor: str
    notes: str
    created_at: datetime
    retired_at: datetime | None = None


class MaskReviewState(ContractModel):
    seed_id: UUID
    expected_count: int
    detected_count: int | None
    draft_count: int
    approved_count: int
    mismatch: bool
    acknowledged: bool
    mask_set_sha256: str
    segmentation_outcome: str | None
    blocking: list[str]
    can_create_contract: bool
    synthetic: bool = False


class JobUsage(ContractModel):
    actual_usd: FiniteNumber = Field(default=0.0, ge=0)
    estimated_usd: FiniteNumber = Field(default=0.0, ge=0)
    unresolved_usd: FiniteNumber = Field(default=0.0, ge=0)
    reserved_usd: FiniteNumber = Field(default=0.0, ge=0)


class FindingUsage(JobUsage):
    calls: int = Field(ge=0)
    target_calls: int = Field(ge=0)
    known_tokens_in: int = Field(ge=0)
    known_tokens_out: int = Field(ge=0)
    unknown_token_calls: int = Field(ge=0)


class JobResult(ContractModel):
    minimization: MinimizationResult | None = None
    retest: RetestComparison | None = None
    outcome: str
    background_asset_id: UUID | None = None
    background_sha256: str | None = None
    original_background_asset_id: UUID | None = None
    original_background_sha256: str | None = None
    background_geometry: dict[str, JsonValue] | None = None
    synthetic: bool = False
    detected_count: int | None = Field(default=None, ge=0)
    expected_count: int | None = Field(default=None, ge=0)
    mask_asset_ids: list[UUID] = Field(default_factory=list)
    parsed_count: int | None = None
    target_status: TargetOutcome | None = None
    diagnostics: list[str] = Field(default_factory=list)
    evidence_asset_ids: list[UUID] = Field(default_factory=list)


class SetupCapabilities(ContractModel):
    provider_mode: Literal["DISABLED", "FIXTURE", "LIVE"]
    synthetic_fixture_enabled: bool
    pricing_revision: str | None
    allowance_configured: bool


JobRef.model_rebuild()


class StoredExperimentEvent(ContractModel):
    """One persisted SSE data frame; replay uses its monotonically increasing ID."""

    id: int = Field(ge=1)
    experiment_id: UUID
    event: str = Field(min_length=1, max_length=100)
    payload: dict[str, JsonValue]
