"""Independent, offline validity gates. Detector evidence is supporting only.

Raw provider media must be normalized in a bounded backend subprocess first.
This package consumes retained opaque PNG bytes and never calls a model or store.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from counterforge_contracts.mutations import (
    GeneratedMutation,
    MutationRecipe,
    Parameters,
    SeedImage,
    canonical_json,
    digest,
    quantize_strength,
)
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.responses import CheckEvidence, ValidationReport
from counterforge_contracts.versions import MutationFamily, ValidityVerdict
from counterforge_contracts.vtc import VisualTestContract
from counterforge_mutations.compositor import decode_png, measure_diff, protected_union
from counterforge_mutations.deterministic import transform
from PIL import Image, ImageChops

VERIFIER_VERSION = "phase04-1"
POLICY_VERSION = "strict-binary-human-gates-1"
STRUCTURAL_CODES = frozenset(
    {"MEDIA", "PROVENANCE", "RECIPE", "MASKS", "GEOMETRY", "PROTECTED_PIXELS"}
)
HUMAN_CODES = frozenset({"HUMAN_SEMANTICS", "HUMAN_VISIBILITY"})


@dataclass(frozen=True)
class VerificationPolicy:
    """No calibrated visibility/semantic threshold exists: require human gates.

    Caller binds optional SAM observations to an immutable supporting artifact.
    A count is never ground truth, including when it matches the contract.
    """

    version: Literal["strict-binary-human-gates-1"] = "strict-binary-human-gates-1"
    sam_outcome: str | None = None
    sam_count: int | None = None
    sam_evidence_sha256: str | None = None
    evidence_asset_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.version != POLICY_VERSION:
            raise ValueError("unsupported verifier policy")
        if self.sam_count is not None and (type(self.sam_count) is not int or self.sam_count < 0):
            raise ValueError("invalid SAM count")
        if self.sam_evidence_sha256 is not None and not _is_digest(self.sam_evidence_sha256):
            raise ValueError("invalid SAM evidence hash")


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def aggregate_verdict(checks: Sequence[CheckEvidence]) -> ValidityVerdict:
    required = [c.status for c in checks if c.mandatory]
    if "FAIL" in required:
        return ValidityVerdict.INVALID
    if not required or any(s != "PASS" for s in required):
        return ValidityVerdict.REVIEW_REQUIRED
    return ValidityVerdict.VALID


def report_digest(report: ValidationReport) -> str:
    return digest(
        canonical_json(report.model_dump(mode="json", exclude={"report_sha256"})).encode()
    )


def seal_report(report: ValidationReport) -> ValidationReport:
    """Derive the verdict from mandatory gates and fingerprint the complete report."""
    report.verdict = aggregate_verdict(report.checks)
    report.reason_codes = [c.code for c in report.checks if c.mandatory and c.status != "PASS"]
    report.report_sha256 = report_digest(report)
    return report


def _hashes(
    contract: VisualTestContract,
    seed: SeedImage,
    mutation: GeneratedMutation,
    policy: VerificationPolicy,
) -> dict[str, str]:
    evidence = {
        "contract": digest(canonical_json(contract.model_dump(mode="json")).encode()),
        "source": digest(seed.png),
        "output": digest(mutation.png),
        "background": digest(mutation.background_png),
        "protected_union": digest(mutation.protected_union_png),
        "diff": digest(mutation.diff_png),
        "recipe": digest(canonical_json(mutation.recipe.model_dump(mode="json")).encode()),
        "seed_identity": digest(
            canonical_json({"asset_id": str(seed.asset_id), "seed_id": str(seed.seed_id)}).encode()
        ),
        "policy": digest(
            canonical_json(
                {
                    "version": policy.version,
                    "sam_outcome": policy.sam_outcome,
                    "sam_count": policy.sam_count,
                    "sam_evidence_sha256": policy.sam_evidence_sha256,
                }
            ).encode()
        ),
    }
    for mask in mutation.reviewed_masks:
        evidence[f"mask:{mask.asset_id}"] = digest(mask.png)
    if policy.sam_evidence_sha256 is not None:
        evidence["sam"] = policy.sam_evidence_sha256
    return evidence


def verify_candidate(
    contract: VisualTestContract,
    seed: SeedImage,
    mutation: GeneratedMutation,
    policy: VerificationPolicy,
) -> ValidationReport:
    # Revalidate mutable Pydantic instances, including model_copy bypasses.
    contract = VisualTestContract.model_validate_json(contract.model_dump_json())
    checks: list[CheckEvidence] = []
    evidence = _hashes(contract, seed, mutation, policy)
    recipe = mutation.recipe

    def add(
        code: str,
        status: Literal["PASS", "FAIL", "UNKNOWN"],
        detail: str,
        *,
        measurements: dict[str, int | str] | None = None,
        mandatory: bool = True,
    ) -> None:
        checks.append(
            CheckEvidence(
                code=code,
                status=status,
                detail=detail,
                measurements=measurements or {},
                mandatory=mandatory,
            )
        )

    source = output = background = union = retained_union = diff_image = None
    try:
        source, output, background = (
            decode_png(seed.png),
            decode_png(mutation.png, normalized=True),
            decode_png(mutation.background_png, normalized=True),
        )
        retained_union = decode_png(mutation.protected_union_png, mask=True, normalized=True)
        diff_image = decode_png(mutation.diff_png, normalized=True)
        if any(
            image.size != source.size for image in (output, background, retained_union, diff_image)
        ) or source.size != (recipe.width, recipe.height):
            raise ValueError("all retained raster dimensions must match source and recipe")
        add("MEDIA", "PASS", "Bounded, opaque, single-frame PNGs with consistent dimensions.")
    except ValueError as exc:
        add("MEDIA", "FAIL", str(exc))

    provenance = (
        seed.sha256 == evidence["source"] == recipe.source_sha256
        and recipe.source_asset_id == seed.asset_id
        and recipe.output_sha256 == evidence["output"]
        and recipe.background_sha256 == evidence["background"]
        and recipe.protected_union_sha256 == evidence["protected_union"]
        and recipe.diff_sha256 == evidence["diff"]
        and recipe.contract_sha256 == evidence["contract"]
    )
    add(
        "PROVENANCE",
        "PASS" if provenance else "FAIL",
        "Byte hashes and source/contract identity must match retained artifacts.",
    )

    proposed = None
    recipe_ok = False
    try:
        recipe = MutationRecipe.model_validate_json(recipe.model_dump_json())
        proposed = MutationPlan.model_validate_json(recipe.plan_json).validate_for_contract(
            contract
        )
        strength = quantize_strength(proposed.strength)
        level = int(Decimal(str(strength)) * 1000)
        algorithm = (
            "supplied_background_v1"
            if recipe.generator == "supplied_background"
            else "brightness_integer_v1"
            if recipe.family == MutationFamily.BRIGHTNESS_SHIFT
            else "rectangles_v1"
            if recipe.family == MutationFamily.BACKGROUND_CLUTTER
            else "solid_rgb_v1"
        )
        expected_parameters = Parameters(
            algorithm=algorithm, level=level, brightness_numerator=500 + level
        )
        software = dict(recipe.software)
        if (
            recipe.family != proposed.family
            or recipe.target_region != proposed.target_region
            or recipe.strength != strength
            or proposed.strength != strength
            or recipe.parameters != expected_parameters
            or recipe.plan_json != canonical_json(proposed.model_dump(mode="json"))
            or type(recipe.rng_seed) is not int
            or len(software) != len(recipe.software)
            or not {"Python", "Pillow", "zlib", "counterforge-mutations"} <= software.keys()
            or any(not value.strip() for value in software.values())
            or software["counterforge-mutations"] != recipe.implementation_version
            or (proposed.requires_muse_image and recipe.generator != "supplied_background")
            or (
                recipe.family == MutationFamily.BRIGHTNESS_SHIFT
                and recipe.generator != "deterministic"
            )
        ):
            raise ValueError("recipe mapping, plan, generator or software provenance mismatch")
        # Replay an independent retained recipe, not the registry's declared metrics.
        if source is not None and background is not None and recipe.generator == "deterministic":
            if (
                transform(source, recipe.parameters, recipe.rng_seed).tobytes()
                != background.tobytes()
            ):
                raise ValueError("deterministic background replay differs")
        add(
            "RECIPE",
            "PASS",
            "Complete versioned plan, integer mapping, software and deterministic replay.",
        )
        recipe_ok = True
    except (ValueError, TypeError) as exc:
        add("RECIPE", "FAIL", str(exc))

    try:
        masks = mutation.reviewed_masks
        if (
            tuple(m.asset_id for m in masks) != tuple(contract.protected.mask_asset_ids)
            or tuple(m.asset_id for m in masks) != recipe.mask_asset_ids
            or tuple(m.sha256 for m in masks) != recipe.mask_sha256s
            or any(m.seed_id != seed.seed_id for m in masks)
        ):
            raise ValueError("mask references, count, ownership or recipe bindings mismatch")
        union = protected_union(seed.png, masks)
        add(
            "MASKS",
            "PASS",
            "Distinct, human-approved binary masks bound to this seed and contract.",
        )
    except ValueError as exc:
        add("MASKS", "FAIL", str(exc))

    if union is None:
        add("GEOMETRY", "UNKNOWN", "Unavailable approved union; mask check blocks acceptance.")
    else:
        # Byte hashes bind retained evidence; geometry compares decoded rasters,
        # independent of the Pillow/zlib compressor used by this deployment.
        geometry_ok = (
            retained_union is not None
            and union.size == retained_union.size
            and union.tobytes() == retained_union.tobytes()
        )
        add(
            "GEOMETRY",
            "PASS" if geometry_ok else "FAIL",
            "Exact reconstructed protected mask geometry; no resize or feathering.",
            measurements={"protected_pixels": union.histogram()[255]},
        )

    if source is None or output is None or union is None or source.size != output.size:
        add(
            "PROTECTED_PIXELS",
            "UNKNOWN",
            "Cannot measure protected pixels without valid media and masks.",
        )
        visibility_values: dict[str, int | str] = {}
    else:
        metrics, diff = measure_diff(source, output, union)
        expected = (
            Image.composite(source, background, union)
            if background is not None and background.size == source.size
            else None
        )
        full_brightness = (
            recipe.family == MutationFamily.BRIGHTNESS_SHIFT
            and recipe.target_region == "full_image"
        )
        if full_brightness and recipe_ok:
            expected = transform(source, recipe.parameters, recipe.rng_seed)
        elif full_brightness:
            expected = None
        pixels_ok = (
            expected is not None
            and expected.tobytes() == output.tobytes()
            and (full_brightness or metrics.protected_changed == 0)
            and metrics == mutation.metrics
            and diff_image is not None
            and decode_png(diff).tobytes() == diff_image.tobytes()
        )
        add(
            "PROTECTED_PIXELS",
            "PASS" if pixels_ok else "FAIL",
            "Exact recorded transform and measured interior/one-pixel contour differences.",
            measurements=asdict(metrics),
        )
        # Measure new 0/255 saturation, without asserting a universal safe level.
        newly_saturated = 0
        for source_channel, output_channel in zip(source.split(), output.split(), strict=True):
            original_extreme = source_channel.point(lambda v: 255 if v in (0, 255) else 0)
            output_extreme = output_channel.point(lambda v: 255 if v in (0, 255) else 0)
            newly_saturated += ImageChops.subtract(output_extreme, original_extreme).histogram()[
                255
            ]
        visibility_values = {
            "newly_saturated_channels": newly_saturated,
            "inner_ring_changed": metrics.inner_ring_changed,
            "outer_ring_changed": metrics.outer_ring_changed,
            "min_visible_fraction": str(contract.protected.min_visible_fraction),
        }

    sam_match = (
        policy.sam_outcome == "COMPLETED"
        and policy.sam_count == contract.expected.value
        and policy.sam_evidence_sha256 is not None
    )
    add(
        "SAM_COUNT",
        "PASS" if sam_match else "UNKNOWN",
        "Supporting evidence only; missing, mismatched or uncertain detector count requires independent human resolution.",
        measurements={
            "observed": policy.sam_count if policy.sam_count is not None else "unknown",
            "expected": contract.expected.value,
            "outcome": policy.sam_outcome or "missing",
        },
        mandatory=False,
    )
    add(
        "HUMAN_SEMANTICS",
        "UNKNOWN",
        "Human must check target count, added/removed same-class instances, masks and semantic integrity.",
    )
    add(
        "HUMAN_VISIBILITY",
        "UNKNOWN",
        "Human must check clipping, seams, blending, occlusion and ambiguity; no calibrated automatic thresholds.",
        measurements=visibility_values,
    )
    return seal_report(
        ValidationReport(
            verdict=ValidityVerdict.REVIEW_REQUIRED,
            verifier_version=VERIFIER_VERSION,
            policy_version=policy.version,
            checks=checks,
            confidence_note="Conservative uncalibrated policy; human semantic and visibility gates are mandatory.",
            evidence_asset_ids=list(
                dict.fromkeys(
                    (
                        seed.asset_id,
                        *(m.asset_id for m in mutation.reviewed_masks),
                        *policy.evidence_asset_ids,
                    )
                )
            ),
            evidence_hashes=evidence,
        )
    )
