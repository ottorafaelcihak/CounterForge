"""Rebuild synthetic Phase 04 fixtures, never evidence of real human approval.

Run with the project environment: python tests/fixtures/phase04/generate.py.
All PNGs and reports are retained; application views use retained bytes only.
"""

import json
from dataclasses import asdict, replace
from pathlib import Path
from uuid import UUID

from counterforge_contracts.mutations import ApprovedMask, SeedImage, canonical_json, digest
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.vtc import VisualTestContract
from counterforge_mutations.compositor import decode_png, encode_png, measure_diff
from counterforge_mutations.registry import approve_plan, generate_mutation
from counterforge_verification.invariants import VerificationPolicy, verify_candidate
from counterforge_verification.review import HumanReview, resolve_review
from PIL import Image, ImageDraw


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def generate(root):
    root.mkdir(parents=True, exist_ok=True)
    source = Image.new("RGB", (64, 32), (220, 220, 220))
    masks = []
    for i in range(5):
        box = (3 + i * 12, 10, 8 + i * 12, 21)
        ImageDraw.Draw(source).rectangle(box, fill=(10, 100, 200))
        mask = Image.new("L", source.size)
        ImageDraw.Draw(mask).rectangle(box, fill=255)
        masks.append(encode_png(mask))
    source_bytes = encode_png(source)
    seed = SeedImage(UUID(int=20), UUID(int=30), source_bytes, digest(source_bytes))
    approved = tuple(
        ApprovedMask(
            UUID(int=i + 1),
            seed.seed_id,
            seed.sha256,
            data,
            digest(data),
            "SYNTHETIC_TEST_MASK_APPROVAL",
        )
        for i, data in enumerate(masks)
    )
    contract = VisualTestContract.model_validate(
        {
            "case_id": "phase04-synthetic-five",
            "prompt": "How many blue rectangles are visible? Return only an integer.",
            "expected": {"value": 5, "reviewer": "SYNTHETIC_TEST_LABEL"},
            "protected": {
                "label": "blue rectangle",
                "expected_instances": 5,
                "mask_asset_ids": [m.asset_id for m in approved],
                "min_visible_fraction": 0.95,
            },
            "allowed_mutations": [
                "background_replacement",
                "background_clutter",
                "brightness_shift",
            ],
            "forbidden_mutations": [
                "object_addition",
                "object_removal",
                "protected_region_repaint",
            ],
            "validation_policy": {},
            "reproduction": {},
            "limits": {},
        }
    )
    write_json(root / "contract.json", contract.model_dump(mode="json"))
    (root / "source.png").write_bytes(source_bytes)
    for i, data in enumerate(masks):
        (root / f"mask-{i + 1}.png").write_bytes(data)
    plan = approve_plan(
        contract,
        MutationPlan(
            hypothesis="Synthetic five-object invariant",
            family="background_replacement",
            strength=0.5,
            prompt="Change background only; no blue rectangles.",
            requires_muse_image=False,
            estimated_cost_usd=0.0,
        ),
    )
    accepted = generate_mutation(
        seed,
        approved,
        plan,
        17,
        background_bytes=encode_png(Image.new("RGB", source.size, (245, 240, 235))),
    )
    removal_image = decode_png(accepted.png)
    ImageDraw.Draw(removal_image).rectangle((3, 10, 8, 21), fill=(245, 240, 235))
    removal_data = encode_png(removal_image)
    removal_metrics, removal_diff = measure_diff(
        source, removal_image, decode_png(accepted.protected_union_png, mask=True)
    )
    removed = replace(
        accepted,
        png=removal_data,
        diff_png=removal_diff,
        metrics=removal_metrics,
        recipe=accepted.recipe.model_copy(
            update={"output_sha256": digest(removal_data), "diff_sha256": digest(removal_diff)}
        ),
    )
    ambiguous = generate_mutation(
        seed,
        approved,
        plan,
        17,
        background_bytes=encode_png(Image.new("RGB", source.size, (10, 100, 200))),
    )
    seam_background = source.copy()
    draw = ImageDraw.Draw(seam_background)
    for i in range(5):
        draw.rectangle((2 + i * 12, 9, 9 + i * 12, 22), outline=(219, 219, 219), width=1)
    seam = generate_mutation(seed, approved, plan, 17, background_bytes=encode_png(seam_background))
    specimens = []
    for name, mutation, expected_auto, simulated_visibility in [
        ("accepted-after-simulated-review", accepted, "REVIEW_REQUIRED", "clear"),
        ("object-removal", removed, "INVALID", None),
        ("ambiguous-visibility", ambiguous, "REVIEW_REQUIRED", "ambiguous"),
        ("seam-only-control", seam, "REVIEW_REQUIRED", None),
    ]:
        base = root / name
        base.mkdir(exist_ok=True)
        for filename, data in [
            ("image.png", mutation.png),
            ("background.png", mutation.background_png),
            ("union.png", mutation.protected_union_png),
            ("diff.png", mutation.diff_png),
        ]:
            (base / filename).write_bytes(data)
        write_json(base / "recipe.json", mutation.recipe.model_dump(mode="json"))
        write_json(base / "metrics.json", asdict(mutation.metrics))
        report = verify_candidate(contract, seed, mutation, VerificationPolicy())
        write_json(base / "automatic-report.json", report.model_dump(mode="json"))
        specimen = {
            "name": name,
            "expected_automatic_verdict": expected_auto,
            "actual_human_review": "PENDING",
        }
        if simulated_visibility is not None:
            decision = HumanReview(
                actor="SYNTHETIC_TEST_REVIEWER",
                note="Simulated test decision only; no real human reviewed this fixture.",
                prior_report_sha256=report.report_sha256,
                evidence_hashes=report.evidence_hashes,
                semantic="consistent",
                visibility=simulated_visibility,
            )
            resolved = resolve_review(report, decision)
            write_json(base / "simulated-review.json", asdict(decision))
            write_json(base / "simulated-report.json", resolved.model_dump(mode="json"))
            specimen["expected_after_simulated_review"] = resolved.verdict.value
        specimen["files"] = {
            str(p.relative_to(root)): digest(p.read_bytes())
            for p in sorted(base.iterdir())
            if p.is_file()
        }
        specimens.append(specimen)
    write_json(
        root / "manifest.json",
        {
            "schema_version": "1.0.0",
            "synthetic": True,
            "live_provider": False,
            "actual_human_review": "PENDING",
            "policy_version": "strict-binary-human-gates-1",
            "seed": {
                "asset_id": str(seed.asset_id),
                "seed_id": str(seed.seed_id),
                "sha256": seed.sha256,
            },
            "contract_sha256": digest(canonical_json(contract.model_dump(mode="json")).encode()),
            "masks": [
                {"asset_id": str(m.asset_id), "file": f"mask-{i + 1}.png", "sha256": m.sha256}
                for i, m in enumerate(approved)
            ],
            "specimens": specimens,
            "limitations": [
                "Synthetic raster fixtures; no live provider calls.",
                "Approval strings and simulated reports exercise code paths only.",
                "Real human acceptance and threshold calibration are outstanding.",
                "Seam-only comparative evaluation is reserved for Phase 08.",
            ],
        },
    )


if __name__ == "__main__":
    generate(Path(__file__).resolve().parent)
