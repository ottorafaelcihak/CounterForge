"""Admit only independently reviewed, redistribution-cleared and frozen assets."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictInt,
    model_validator,
)

from services.api.app.artifacts import normalize_image

from .integrity import aware_past, real_text, seal, verify

Text = Annotated[str, AfterValidator(real_text)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ReviewTime = Annotated[
    datetime, AfterValidator(aware_past), PlainSerializer(lambda v: v.isoformat(), return_type=str)
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Rights(FrozenModel):
    source: Text
    permission: Text
    redistribution_cleared: Literal[True]
    reviewer: Text
    reviewed_at: ReviewTime


class SeedEntry(FrozenModel):
    case_id: Text
    split: Literal["development", "heldout"]
    source_path: Text
    normalized_path: Text
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    source_sha256: Hash
    normalized_sha256: Hash
    width: StrictInt = Field(ge=1, le=4096)
    height: StrictInt = Field(ge=1, le=4096)
    verified_count: StrictInt = Field(ge=1, le=16)
    rights: Rights
    label_reviewer: Text
    label_reviewed_at: ReviewTime
    ambiguity: Literal["clear"]
    ambiguity_notes: Text


class Exclusion(FrozenModel):
    case_id: Text
    source_sha256: Hash
    reason: Text


class DatasetManifest(FrozenModel):
    schema_version: Literal["1.0"]
    revision: Text
    contract_version: Literal["1.0.0"]
    policy_revision: Literal["strict-binary-human-gates-1"]
    entries: tuple[SeedEntry, ...]
    exclusions: tuple[Exclusion, ...]
    sha256: Hash

    @model_validator(mode="after")
    def membership(self) -> Self:
        if (
            sum(e.split == "development" for e in self.entries) != 8
            or sum(e.split == "heldout" for e in self.entries) != 4
        ):
            raise ValueError("Exactly 8 development and 4 heldout cases are required")
        ids = [e.case_id.casefold() for e in self.entries] + [
            e.case_id.casefold() for e in self.exclusions
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate case or split overlap")
        for field in ("source_sha256", "normalized_sha256"):
            values = [getattr(e, field) for e in self.entries]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate image bytes or split overlap")
        return self


def asset_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or any(
        p == ".env" or p.startswith(".env.") or p.endswith("-live") for p in path.parts
    ):
        raise ValueError("Asset path must stay within the explicit non-live dataset root")
    return path


def validate_dataset(manifest: DatasetManifest, root: Path) -> DatasetManifest:
    verify(manifest.model_dump(mode="json"))
    for entry in manifest.entries:
        source = asset_path(root, entry.source_path).read_bytes()
        normalized = asset_path(root, entry.normalized_path).read_bytes()
        if (
            sha256(source).hexdigest() != entry.source_sha256
            or sha256(normalized).hexdigest() != entry.normalized_sha256
        ):
            raise ValueError("Dataset asset hash mismatch")
        image = normalize_image(source, entry.media_type)
        if image.png != normalized or (image.width, image.height) != (entry.width, entry.height):
            raise ValueError(
                "Normalized bytes/dimensions differ from registered normalization policy"
            )
    return manifest


def freeze(value: dict[str, Any], root: Path) -> DatasetManifest:
    manifest = DatasetManifest.model_validate(seal(value))
    # Seal canonical typed representation (not caller-specific number/time encodings).
    manifest = DatasetManifest.model_validate(seal(manifest.model_dump(mode="json")))
    return validate_dataset(manifest, root)
