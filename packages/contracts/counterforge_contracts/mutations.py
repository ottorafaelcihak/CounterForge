"""Pure immutable mutation boundary. Artifact persistence belongs to the caller."""

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from .base import ContractModel, FiniteNumber
from .versions import MutationFamily


class FrozenContract(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Parameters(FrozenContract):
    algorithm: Literal[
        "solid_rgb_v1", "rectangles_v1", "brightness_integer_v1", "supplied_background_v1"
    ]
    color_space: Literal["sRGB_encoded_integer"] = "sRGB_encoded_integer"
    strength_quantization: Literal["decimal_4dp_ROUND_HALF_UP"] = "decimal_4dp_ROUND_HALF_UP"
    replacement_mapping: Literal["floor((source*(1000-level)+sampled_rgb*level)/1000)"] = (
        "floor((source*(1000-level)+sampled_rgb*level)/1000)"
    )
    png_compress_level: Literal[9] = 9
    png_optimize: Literal[False] = False
    png_metadata: Literal["strip_via_fresh_frombytes"] = "strip_via_fresh_frombytes"
    strength_mapping: Literal["floor(1000*strength); gain=500+level; rectangles=1+level//100"] = (
        "floor(1000*strength); gain=500+level; rectangles=1+level//100"
    )
    level: int = Field(ge=0, le=1000)
    brightness_numerator: int = Field(ge=500, le=1500)
    brightness_denominator: Literal[1000] = 1000
    rounding: Literal["floor"] = "floor"
    clipping: Literal["clamp_0_255"] = "clamp_0_255"
    prng: Literal["python.random.Random_MT19937"] = "python.random.Random_MT19937"
    ring_width: Literal[1] = 1
    blend_policy: Literal["strict_binary_original"] = "strict_binary_original"


class MutationRecipe(FrozenContract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    implementation_version: Literal["phase04-1"] = "phase04-1"
    family: MutationFamily
    strength: FiniteNumber = Field(ge=0, le=1)
    target_region: Literal["background_only", "full_image"]
    rng_seed: int
    plan_json: str
    contract_sha256: str
    source_asset_id: UUID
    source_sha256: str
    output_sha256: str
    background_sha256: str
    mask_asset_ids: tuple[UUID, ...]
    mask_sha256s: tuple[str, ...]
    protected_union_sha256: str
    diff_sha256: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    parameters: Parameters
    software: tuple[tuple[str, str], ...]
    generator: Literal["deterministic", "supplied_background"]


@dataclass(frozen=True)
class SeedImage:
    asset_id: UUID
    seed_id: UUID
    png: bytes
    sha256: str


@dataclass(frozen=True)
class ApprovedMask:
    asset_id: UUID
    seed_id: UUID
    source_sha256: str
    png: bytes
    sha256: str
    approved_by: str


@dataclass(frozen=True)
class ApprovedMutationPlan:
    """Validated snapshot, rechecked by registry against its retained VTC."""

    plan_json: str
    contract_json: str
    contract_sha256: str


@dataclass(frozen=True)
class DiffMetrics:
    protected_pixels: int
    protected_changed: int
    interior_pixels: int
    interior_changed: int
    inner_ring_pixels: int
    inner_ring_changed: int
    outer_ring_pixels: int
    outer_ring_changed: int
    max_channel_delta: int
    absolute_channel_delta: int


@dataclass(frozen=True)
class CompositeResult:
    png: bytes
    protected_union_png: bytes
    diff_png: bytes
    metrics: DiffMetrics


@dataclass(frozen=True)
class GeneratedMutation:
    png: bytes
    recipe: MutationRecipe
    protected_union_png: bytes
    diff_png: bytes
    background_png: bytes
    reviewed_masks: tuple[ApprovedMask, ...]
    metrics: DiffMetrics


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> str:
    """Same canonical representation as the backend artifact fingerprints."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def quantize_strength(strength: float) -> float:
    """Match PostgreSQL NUMERIC(*,4) before mapping to integer parameters."""
    return float(Decimal(str(strength)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
