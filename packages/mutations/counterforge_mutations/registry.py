"""Bounded pure registry. Backend reserves providers and persists returned bytes."""

import platform
import zlib
from collections.abc import Sequence
from decimal import Decimal

from counterforge_contracts.mutations import (
    ApprovedMask,
    ApprovedMutationPlan,
    GeneratedMutation,
    MutationRecipe,
    Parameters,
    SeedImage,
    canonical_json,
    digest,
    quantize_strength,
)
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.versions import MutationFamily
from counterforge_contracts.vtc import VisualTestContract
from PIL import __version__ as pillow_version

from .compositor import composite_background, decode_png, encode_png, measure_diff, protected_union
from .deterministic import transform


def approve_plan(contract: VisualTestContract, plan: MutationPlan) -> ApprovedMutationPlan:
    contract = VisualTestContract.model_validate_json(contract.model_dump_json())
    plan = MutationPlan.model_validate_json(plan.model_dump_json()).validate_for_contract(contract)
    plan.strength = quantize_strength(plan.strength)
    contract_json = canonical_json(contract.model_dump(mode="json"))
    return ApprovedMutationPlan(
        canonical_json(plan.model_dump(mode="json")), contract_json, digest(contract_json.encode())
    )


def generate_mutation(
    seed_asset: SeedImage,
    reviewed_masks: Sequence[ApprovedMask],
    plan: ApprovedMutationPlan,
    rng_seed: int,
    *,
    background_bytes: bytes | None = None,
) -> GeneratedMutation:
    if not isinstance(plan, ApprovedMutationPlan) or type(rng_seed) is not int:
        raise ValueError("typed approved plan and integer seed required")
    contract = VisualTestContract.model_validate_json(plan.contract_json)
    proposed = MutationPlan.model_validate_json(plan.plan_json).validate_for_contract(contract)
    if (
        digest(plan.contract_json.encode()) != plan.contract_sha256
        or digest(seed_asset.png) != seed_asset.sha256
    ):
        raise ValueError("source/contract checksum mismatch")
    masks = tuple(reviewed_masks)
    if tuple(m.asset_id for m in masks) != tuple(contract.protected.mask_asset_ids) or any(
        m.seed_id != seed_asset.seed_id for m in masks
    ):
        raise ValueError("mask ownership/references")
    source = decode_png(seed_asset.png)
    union = protected_union(seed_asset.png, masks)
    if proposed.requires_muse_image and background_bytes is None:
        raise ValueError("provider background bytes required; registry never invokes providers")
    if background_bytes is not None and proposed.family == MutationFamily.BRIGHTNESS_SHIFT:
        raise ValueError("brightness family cannot accept background provider output")
    proposed.strength = quantize_strength(proposed.strength)
    level = int(Decimal(str(proposed.strength)) * 1000)
    algorithm = (
        "supplied_background_v1"
        if background_bytes is not None
        else "brightness_integer_v1"
        if proposed.family == MutationFamily.BRIGHTNESS_SHIFT
        else "rectangles_v1"
        if proposed.family == MutationFamily.BACKGROUND_CLUTTER
        else "solid_rgb_v1"
    )
    parameters = Parameters(algorithm=algorithm, level=level, brightness_numerator=500 + level)
    background = (
        background_bytes
        if background_bytes is not None
        else encode_png(transform(source, parameters, rng_seed))
    )
    if (
        proposed.family == MutationFamily.BRIGHTNESS_SHIFT
        and proposed.target_region == "full_image"
    ):
        output = decode_png(background)
        metrics, diff = measure_diff(source, output, union)
        image_bytes, union_bytes = encode_png(output), encode_png(union)
    else:
        composite = composite_background(seed_asset.png, background, masks)
        image_bytes, union_bytes, diff, metrics = (
            composite.png,
            composite.protected_union_png,
            composite.diff_png,
            composite.metrics,
        )
    recipe = MutationRecipe(
        family=proposed.family,
        strength=proposed.strength,
        target_region=proposed.target_region,
        rng_seed=rng_seed,
        plan_json=canonical_json(proposed.model_dump(mode="json")),
        contract_sha256=plan.contract_sha256,
        source_asset_id=seed_asset.asset_id,
        source_sha256=seed_asset.sha256,
        output_sha256=digest(image_bytes),
        background_sha256=digest(background),
        mask_asset_ids=tuple(m.asset_id for m in masks),
        mask_sha256s=tuple(m.sha256 for m in masks),
        protected_union_sha256=digest(union_bytes),
        diff_sha256=digest(diff),
        width=source.width,
        height=source.height,
        parameters=parameters,
        software=(
            ("Python", platform.python_version()),
            ("Pillow", pillow_version),
            ("zlib", zlib.ZLIB_RUNTIME_VERSION),
            ("counterforge-mutations", "phase04-1"),
        ),
        generator="supplied_background" if background_bytes is not None else "deterministic",
    )
    return GeneratedMutation(image_bytes, recipe, union_bytes, diff, background, masks, metrics)
