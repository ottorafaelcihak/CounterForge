"""Public versioned regression format. Private provider records are never accepted."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from counterforge_contracts.base import ContractModel, FiniteNumber
from pydantic import Field, JsonValue


class Expected(ContractModel):
    kind: Literal["integer"] = "integer"
    value: int = Field(strict=True, ge=0, le=1000)
    verified_by: Literal["human"] = "human"


class Model(ContractModel):
    adapter: Literal["META_SPARK", "LOCAL_FIXTURE"]
    model_id: str
    revision: str
    sampling_options: dict[str, JsonValue]


class Verification(ContractModel):
    verdict: Literal["VALID"]
    reviewed_by: str
    verifier_version: str
    report_sha256: str | None = None


class Confirmation(ContractModel):
    screening_failures: Literal[1]
    fresh_attempts: Literal[3]
    fresh_failures: Literal[3]


class Mutation(ContractModel):
    family: str
    strength: FiniteNumber = Field(ge=0, le=1)
    recipe_version: Literal["1.0.0"] = "1.0.0"


class RegressionManifest(ContractModel):
    format_version: Literal["1.0.0"] = "1.0.0"
    finding_id: UUID
    export_job_id: UUID
    test_id: str
    task: Literal["object_counting"] = "object_counting"
    question: str = Field(min_length=1, max_length=2000)
    expected: Expected
    model: Model
    invariant_verification: Verification
    confirmation: Confirmation
    mutation: Mutation
    provenance: Literal["LOCAL_FIXTURE", "REAL_PROVIDER"]
    files: dict[str, str] = Field(
        default_factory=lambda: {
            "original": "evidence/original.png",
            "failing": "evidence/failing.png",
            "protected_mask": "evidence/protected-mask.png",
            "comparison": "evidence/comparison.png",
            "target_responses": "evidence/target-responses.json",
            "validity_report": "evidence/validity-report.json",
            "recipe": "evidence/recipe.json",
        }
    )
    sha256: dict[str, str] = Field(default_factory=dict)
    recorded_at: datetime
    limitation: str = "Re-running a provider-managed model may not reproduce historical behavior. LOCAL_FIXTURE evidence is simulated, never live reproduction."
