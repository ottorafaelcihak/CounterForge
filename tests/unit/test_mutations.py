import io
from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest
from PIL import Image


def png(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def scene(vtc_payload):
    from counterforge_contracts.mutations import ApprovedMask, SeedImage
    from counterforge_contracts.vtc import VisualTestContract

    payload = dict(vtc_payload)
    payload["expected"] = dict(payload["expected"], value=1)
    payload["protected"] = dict(
        payload["protected"], expected_instances=1, mask_asset_ids=[str(UUID(int=1))]
    )
    contract = VisualTestContract.model_validate(payload)
    source = Image.new("RGB", (9, 9), (30, 40, 50))
    for y in range(2, 7):
        for x in range(2, 7):
            source.putpixel((x, y), (10, 100, 200))
    data = png(source)
    mask = Image.new("L", (9, 9))
    for y in range(2, 7):
        for x in range(2, 7):
            mask.putpixel((x, y), 255)
    mask_bytes = png(mask)
    seed = SeedImage(UUID(int=20), UUID(int=30), data, sha256(data).hexdigest())
    approved = ApprovedMask(
        UUID(int=1),
        seed.seed_id,
        seed.sha256,
        mask_bytes,
        sha256(mask_bytes).hexdigest(),
        "reviewer",
    )
    return contract, seed, (approved,)


def plan(contract, family="background_replacement", strength=0.5, **kwargs):
    from counterforge_contracts.plans import MutationPlan
    from counterforge_mutations.registry import approve_plan

    return approve_plan(
        contract,
        MutationPlan(
            hypothesis="count invariant",
            family=family,
            strength=strength,
            prompt="change context",
            requires_muse_image=False,
            estimated_cost_usd=0.0,
            **kwargs,
        ),
    )


def test_replay_preserves_original_pixels_and_records_hashes(vtc_payload):
    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    approved = plan(contract)
    result = generate_mutation(seed, masks, approved, 17)
    replay = generate_mutation(seed, masks, approved, 17)
    assert result == replay
    output = Image.open(io.BytesIO(result.png))
    assert output.size == (9, 9)
    assert output.getpixel((4, 4)) == (10, 100, 200)
    assert output.getpixel((0, 0)) != (30, 40, 50)
    assert result.recipe.output_sha256 == sha256(result.png).hexdigest()
    assert result.recipe.source_sha256 == seed.sha256
    assert result.recipe.mask_sha256s == (masks[0].sha256,)
    assert dict(result.recipe.software)["Pillow"]


@pytest.mark.parametrize(
    "family", ["background_replacement", "background_clutter", "brightness_shift"]
)
@pytest.mark.parametrize("strength", [0.0, 1.0])
def test_all_p0_families_replay_at_bounds(vtc_payload, family, strength):
    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    approved = plan(contract, family, strength)
    assert generate_mutation(seed, masks, approved, 4) == generate_mutation(
        seed, masks, approved, 4
    )


@pytest.mark.parametrize("strength", [-0.01, 1.01, float("nan")])
def test_rejects_unbounded_strength(vtc_payload, strength):
    contract, _, _ = scene(vtc_payload)
    with pytest.raises(ValueError):
        plan(contract, strength=strength)


def test_rejects_disallowed_family_and_wrong_source_mask(vtc_payload):
    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    with pytest.raises(ValueError):
        plan(contract, "object_removal")
    limited = contract.model_copy(update={"allowed_mutations": []})
    with pytest.raises(ValueError):
        plan(limited)
    with pytest.raises(ValueError):
        generate_mutation(seed, (replace(masks[0], source_sha256="0" * 64),), plan(contract), 1)


def test_brightness_foreground_only_for_explicit_full_image(vtc_payload):
    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    result = generate_mutation(
        seed, masks, plan(contract, "brightness_shift", 1.0, target_region="full_image"), 1
    )
    assert Image.open(io.BytesIO(result.png)).getpixel((4, 4)) == (15, 150, 255)
    assert result.recipe.parameters.brightness_numerator == 1500
    assert result.recipe.parameters.clipping == "clamp_0_255"


def test_strength_rounds_to_database_precision_before_integer_mapping(vtc_payload):
    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    result = generate_mutation(seed, masks, plan(contract, "brightness_shift", 0.12395), 1)
    assert result.recipe.strength == 0.124
    assert result.recipe.parameters.level == 124
    assert "0.124" in result.recipe.plan_json


def test_contract_hash_uses_backend_canonical_json(vtc_payload):
    import json

    contract, _, _ = scene(vtc_payload)
    approved = plan(contract)
    canonical = json.dumps(
        contract.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    assert approved.contract_sha256 == sha256(canonical).hexdigest()


def test_rejects_raw_plan_bad_source_and_cross_seed_masks(vtc_payload):
    from counterforge_contracts.plans import MutationPlan
    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    with pytest.raises(ValueError):
        generate_mutation(
            seed, masks, MutationPlan.model_validate_json(plan(contract).plan_json), 1
        )
    with pytest.raises(ValueError):
        generate_mutation(replace(seed, sha256="0" * 64), masks, plan(contract), 1)
    with pytest.raises(ValueError):
        generate_mutation(seed, (replace(masks[0], seed_id=seed.asset_id),), plan(contract), 1)


def test_supplied_background_requires_typed_provider_plan_and_never_runs_provider(vtc_payload):
    from counterforge_contracts.plans import MutationPlan
    from counterforge_mutations.registry import approve_plan, generate_mutation

    contract, seed, masks = scene(vtc_payload)
    proposed = MutationPlan.model_validate_json(plan(contract).plan_json)
    proposed.requires_muse_image = True
    approved = approve_plan(contract, proposed)
    with pytest.raises(ValueError, match="background bytes required"):
        generate_mutation(seed, masks, approved, 3)
    background = png(Image.new("RGB", (9, 9), "white"))
    result = generate_mutation(seed, masks, approved, 3, background_bytes=background)
    assert result.background_png == background
    assert result.recipe.generator == "supplied_background"
    assert result.recipe.parameters.algorithm == "supplied_background_v1"
    assert result.metrics.protected_changed == 0


def test_replacement_strength_zero_is_identity_and_normalizer_is_reproducible(vtc_payload):
    import zlib

    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    result = generate_mutation(seed, masks, plan(contract, strength=0.0), 17)
    assert (
        Image.open(io.BytesIO(result.png)).tobytes() == Image.open(io.BytesIO(seed.png)).tobytes()
    )
    assert dict(result.recipe.software)["zlib"] == zlib.ZLIB_RUNTIME_VERSION
    assert result.recipe.parameters.png_compress_level == 9
    assert result.recipe.parameters.strength_quantization == "decimal_4dp_ROUND_HALF_UP"
