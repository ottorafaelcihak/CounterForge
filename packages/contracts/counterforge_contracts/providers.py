from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, StrictInt, model_validator

from .base import ContractModel, FiniteNumber
from .versions import TargetOutcome


class ProviderResult(ContractModel):
    """Internal transport metadata; never an API response DTO."""

    provider: str
    model: str
    request_id: str | None = None
    raw_asset_key: str | None = None
    usage: dict[str, JsonValue] = Field(default_factory=dict)
    latency_ms: StrictInt = Field(ge=0)
    warnings: tuple[str, ...] = ()


class SegmentedObject(ContractModel):
    object_id: str
    bbox_xyxy: tuple[FiniteNumber, FiniteNumber, FiniteNumber, FiniteNumber]
    mask_asset_id: UUID
    confidence_or_null: FiniteNumber | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_box(self) -> Self:
        x1, y1, x2, y2 = self.bbox_xyxy
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("bounding box must have nonempty positive geometry")
        return self


class SegmentationResult(ContractModel):
    objects: list[SegmentedObject]
    outcome: Literal["COMPLETED", "INCOMPLETE", "REFUSED", "PROVIDER_ERROR"]
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def completed_has_geometry(self) -> Self:
        if self.outcome == "COMPLETED" and not self.objects:
            raise ValueError("completed segmentation must contain objects")
        return self


class TargetResult(ContractModel):
    raw_text: str
    parsed_answer: StrictInt | None
    status: TargetOutcome
    model_id: str
    version_claim: str
    options: dict[str, JsonValue]
    latency_ms: StrictInt = Field(ge=0)
    request_id: str | None = None
    raw_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def interpreted_verdict_requires_answer(self) -> Self:
        if (
            self.status in (TargetOutcome.CORRECT, TargetOutcome.INCORRECT)
            and self.parsed_answer is None
        ):
            raise ValueError("count correctness requires an interpretable answer")
        if (
            self.status in (TargetOutcome.UNKNOWN, TargetOutcome.PROVIDER_ERROR)
            and self.parsed_answer is not None
        ):
            raise ValueError("unknown/error outcomes cannot carry an asserted count")
        return self
