"""Independent gates: favorable detector scores cannot excuse failed evidence."""

from dataclasses import replace
from itertools import product

import pytest
from PIL import Image

from tests.unit.test_mutations import plan, png, scene


def generated(vtc_payload, **kwargs):
    from counterforge_mutations.registry import generate_mutation

    contract, seed, masks = scene(vtc_payload)
    return contract, seed, generate_mutation(seed, masks, plan(contract, **kwargs), 17)


def check(report, code):
    return next(c for c in report.checks if c.code == code)


def review(report, **changes):
    from counterforge_verification.review import HumanReview

    values = dict(
        actor="test-fixture-reviewer",
        note="Synthetic decision for unit testing only.",
        prior_report_sha256=report.report_sha256,
        evidence_hashes=dict(report.evidence_hashes),
        semantic="consistent",
        visibility="clear",
    )
    return HumanReview(**(values | changes))


def test_mandatory_gate_truth_table():
    from counterforge_contracts.responses import CheckEvidence
    from counterforge_verification.invariants import aggregate_verdict

    for statuses in product(("PASS", "FAIL", "UNKNOWN", "REQUIRED"), repeat=3):
        checks = [CheckEvidence(code=str(i), status=s) for i, s in enumerate(statuses)]
        expected = (
            "INVALID"
            if "FAIL" in statuses
            else "REVIEW_REQUIRED"
            if set(statuses) & {"UNKNOWN", "REQUIRED"}
            else "VALID"
        )
        assert aggregate_verdict(checks) == expected
    assert aggregate_verdict([]) == "REVIEW_REQUIRED"
    assert (
        aggregate_verdict(
            [
                CheckEvidence(code="required", status="PASS"),
                CheckEvidence(code="support", status="FAIL", mandatory=False),
            ]
        )
        == "VALID"
    )


def test_matching_sam_never_certifies_semantics_or_visibility(vtc_payload):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate

    contract, seed, mutation = generated(vtc_payload)
    policy = VerificationPolicy(sam_outcome="COMPLETED", sam_count=1, sam_evidence_sha256="a" * 64)
    report = verify_candidate(contract, seed, mutation, policy)
    assert report.verdict == "REVIEW_REQUIRED"
    assert check(report, "SAM_COUNT").status == "PASS"
    assert not check(report, "SAM_COUNT").mandatory
    assert check(report, "HUMAN_SEMANTICS").status == "UNKNOWN"
    assert check(report, "HUMAN_VISIBILITY").status == "UNKNOWN"
    assert check(report, "PROTECTED_PIXELS").measurements["inner_ring_changed"] == 0
    assert report.policy_version
    assert len(report.report_sha256) == 64


def test_bound_human_review_can_accept_background(vtc_payload):
    from counterforge_verification.invariants import (
        VerificationPolicy,
        report_digest,
        verify_candidate,
    )
    from counterforge_verification.review import resolve_review

    contract, seed, mutation = generated(vtc_payload)
    before = verify_candidate(contract, seed, mutation, VerificationPolicy())
    report = resolve_review(before, review(before))
    assert before.verdict == "REVIEW_REQUIRED"
    assert report.verdict == "VALID"
    assert report.review_audit.actor == "test-fixture-reviewer"
    assert report.review_audit.previous_report_sha256 == before.report_sha256
    assert report.review_audit.reviewed_evidence_hashes == before.evidence_hashes
    assert report_digest(report) == report.report_sha256 != before.report_sha256


@pytest.mark.parametrize(
    "kind,code",
    [
        ("output_hash", "PROVENANCE"),
        ("source_hash", "PROVENANCE"),
        ("background_hash", "PROVENANCE"),
        ("union_hash", "PROVENANCE"),
        ("diff_hash", "PROVENANCE"),
        ("mask_owner", "MASKS"),
        ("mask_approval", "MASKS"),
        ("mask_duplicate", "MASKS"),
        ("recipe", "RECIPE"),
        ("dimensions", "MEDIA"),
    ],
)
def test_tampered_evidence_never_passes(vtc_payload, kind, code):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate

    contract, seed, mutation = generated(vtc_payload)
    if kind == "source_hash":
        seed = replace(seed, sha256="0" * 64)
    elif kind.endswith("_hash"):
        field = {
            "output_hash": "png",
            "background_hash": "background_png",
            "union_hash": "protected_union_png",
            "diff_hash": "diff_png",
        }[kind]
        mutation = replace(mutation, **{field: getattr(mutation, field) + b"changed"})
    elif kind in ("mask_owner", "mask_approval", "mask_duplicate"):
        mask = mutation.reviewed_masks[0]
        mask = (
            replace(mask, seed_id=seed.asset_id)
            if kind == "mask_owner"
            else replace(mask, approved_by="")
            if kind == "mask_approval"
            else mask
        )
        mutation = replace(
            mutation, reviewed_masks=(mask, mask) if kind == "mask_duplicate" else (mask,)
        )
    elif kind == "recipe":
        mutation = replace(mutation, recipe=mutation.recipe.model_copy(update={"software": ()}))
    else:
        mutation = replace(mutation, png=png(Image.new("RGB", (8, 9))))
    report = verify_candidate(contract, seed, mutation, VerificationPolicy())
    assert report.verdict == "INVALID"
    assert check(report, code).status == "FAIL"


def test_removal_cannot_be_overridden_and_audit_preserves_structural_fail(vtc_payload):
    from counterforge_contracts.mutations import digest
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate
    from counterforge_verification.review import GroundTruthAudit, resolve_review

    contract, seed, mutation = generated(vtc_payload)
    removed = png(Image.new("RGB", (9, 9), (30, 40, 50)))
    mutation = replace(
        mutation,
        png=removed,
        recipe=mutation.recipe.model_copy(update={"output_sha256": digest(removed)}),
    )
    report = verify_candidate(contract, seed, mutation, VerificationPolicy())
    assert report.verdict == "INVALID"
    assert check(report, "PROTECTED_PIXELS").status == "FAIL"
    with pytest.raises(ValueError, match="audit"):
        resolve_review(report, review(report))
    audit = GroundTruthAudit(
        actor="auditor",
        note="Test-only audit confirms the immutable evidence.",
        prior_report_sha256=report.report_sha256,
        evidence_hashes=dict(report.evidence_hashes),
        resolved_check_codes=(),
    )
    assert resolve_review(report, review(report, ground_truth_audit=audit)).verdict == "INVALID"


@pytest.mark.parametrize(
    "semantic,visibility,expected",
    [
        ("additional_object", "clear", "INVALID"),
        ("removed_object", "clear", "INVALID"),
        ("ambiguous", "clear", "REVIEW_REQUIRED"),
        ("consistent", "ambiguous", "REVIEW_REQUIRED"),
    ],
)
def test_human_semantic_and_visibility_resolution(vtc_payload, semantic, visibility, expected):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate
    from counterforge_verification.review import resolve_review

    contract, seed, mutation = generated(vtc_payload)
    report = verify_candidate(contract, seed, mutation, VerificationPolicy())
    assert (
        resolve_review(report, review(report, semantic=semantic, visibility=visibility)).verdict
        == expected
    )


@pytest.mark.parametrize(
    "change",
    [{"actor": ""}, {"note": " "}, {"prior_report_sha256": "0" * 64}, {"evidence_hashes": {}}],
)
def test_review_rejects_unbound_or_anonymous_decision(vtc_payload, change):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate
    from counterforge_verification.review import resolve_review

    contract, seed, mutation = generated(vtc_payload)
    report = verify_candidate(contract, seed, mutation, VerificationPolicy())
    with pytest.raises(ValueError):
        resolve_review(report, review(report, **change))


def test_full_image_brightness_records_clipping_and_requires_visibility(vtc_payload):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate

    contract, seed, mutation = generated(
        vtc_payload, family="brightness_shift", strength=1.0, target_region="full_image"
    )
    report = verify_candidate(contract, seed, mutation, VerificationPolicy())
    assert report.verdict == "REVIEW_REQUIRED"
    assert check(report, "PROTECTED_PIXELS").status == "PASS"
    assert check(report, "HUMAN_VISIBILITY").measurements["newly_saturated_channels"] == 25


def test_sixth_target_in_background_is_not_accepted_by_preservation(vtc_payload):
    from hashlib import sha256
    from uuid import UUID

    from counterforge_contracts.mutations import ApprovedMask, SeedImage
    from counterforge_contracts.vtc import VisualTestContract
    from counterforge_mutations.registry import generate_mutation
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate
    from counterforge_verification.review import resolve_review

    payload = dict(vtc_payload)
    payload["protected"] = dict(
        payload["protected"], mask_asset_ids=[str(UUID(int=i)) for i in range(1, 6)]
    )
    contract = VisualTestContract.model_validate(payload)
    source = Image.new("RGB", (24, 12), "white")
    masks = []
    for i in range(5):
        mask = Image.new("L", source.size)
        for y in range(2, 5):
            for x in range(1 + 4 * i, 4 + 4 * i):
                source.putpixel((x, y), (0, 0, 255))
                mask.putpixel((x, y), 255)
        masks.append(png(mask))
    source_bytes = png(source)
    seed = SeedImage(UUID(int=20), UUID(int=30), source_bytes, sha256(source_bytes).hexdigest())
    approved = tuple(
        ApprovedMask(
            UUID(int=i + 1), seed.seed_id, seed.sha256, data, sha256(data).hexdigest(), "fixture"
        )
        for i, data in enumerate(masks)
    )
    background = Image.new("RGB", source.size, "white")
    for y in range(8, 11):
        for x in range(2, 5):
            background.putpixel((x, y), (0, 0, 255))
    mutation = generate_mutation(
        seed, approved, plan(contract), 1, background_bytes=png(background)
    )
    assert mutation.metrics.protected_changed == 0
    report = verify_candidate(
        contract,
        seed,
        mutation,
        VerificationPolicy(sam_outcome="COMPLETED", sam_count=6, sam_evidence_sha256="a" * 64),
    )
    assert report.verdict == "REVIEW_REQUIRED"
    assert check(report, "SAM_COUNT").status == "UNKNOWN"
    assert resolve_review(report, review(report, semantic="additional_object")).verdict == "INVALID"


@pytest.mark.parametrize(
    "kind", ["mapping", "plan_region", "wrong_family", "mask_hash", "metrics", "geometry", "replay"]
)
def test_independent_recipe_and_geometry_checks_do_not_trust_declared_evidence(vtc_payload, kind):
    from counterforge_contracts.mutations import digest
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate

    contract, seed, mutation = generated(vtc_payload)
    if kind == "mapping":
        recipe = mutation.recipe.model_copy(
            update={"parameters": mutation.recipe.parameters.model_copy(update={"level": 999})}
        )
        mutation = replace(mutation, recipe=recipe)
    elif kind == "plan_region":
        mutation = replace(
            mutation, recipe=mutation.recipe.model_copy(update={"target_region": "full_image"})
        )
    elif kind == "wrong_family":
        from counterforge_contracts.versions import MutationFamily

        contract = contract.model_copy(
            update={"allowed_mutations": [MutationFamily.BRIGHTNESS_SHIFT]}
        )
    elif kind == "mask_hash":
        mutation = replace(
            mutation, reviewed_masks=(replace(mutation.reviewed_masks[0], sha256="0" * 64),)
        )
    elif kind == "metrics":
        mutation = replace(mutation, metrics=replace(mutation.metrics, outer_ring_changed=0))
    elif kind == "geometry":
        data = png(Image.new("L", (9, 9), 255))
        mutation = replace(
            mutation,
            protected_union_png=data,
            recipe=mutation.recipe.model_copy(update={"protected_union_sha256": digest(data)}),
        )
    else:
        data = png(Image.new("RGB", (9, 9), "white"))
        mutation = replace(
            mutation,
            background_png=data,
            recipe=mutation.recipe.model_copy(update={"background_sha256": digest(data)}),
        )
    assert verify_candidate(contract, seed, mutation, VerificationPolicy()).verdict == "INVALID"


def test_invalid_human_count_can_only_be_corrected_by_bound_ground_truth_audit(vtc_payload):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate
    from counterforge_verification.review import GroundTruthAudit, resolve_review

    contract, seed, mutation = generated(vtc_payload)
    initial = verify_candidate(contract, seed, mutation, VerificationPolicy())
    invalid = resolve_review(initial, review(initial, semantic="additional_object"))
    with pytest.raises(ValueError, match="audit"):
        resolve_review(invalid, review(invalid))
    audit = GroundTruthAudit(
        actor="auditor",
        note="Synthetic test-only correction of semantic evidence.",
        prior_report_sha256=invalid.report_sha256,
        evidence_hashes=dict(invalid.evidence_hashes),
        resolved_check_codes=("HUMAN_SEMANTICS",),
    )
    corrected = resolve_review(invalid, review(invalid, ground_truth_audit=audit))
    assert corrected.verdict == "VALID"
    assert len(corrected.review_audit.ground_truth_audit_sha256) == 64
    with pytest.raises(ValueError):
        resolve_review(
            invalid,
            review(
                invalid,
                ground_truth_audit=replace(audit, resolved_check_codes=("PROTECTED_PIXELS",)),
            ),
        )


def test_review_rejects_forged_prior_verdict_and_missing_gates(vtc_payload):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate
    from counterforge_verification.review import resolve_review

    contract, seed, mutation = generated(vtc_payload)
    report = verify_candidate(contract, seed, mutation, VerificationPolicy())
    decision = review(report)
    from counterforge_contracts.versions import ValidityVerdict

    report.verdict = ValidityVerdict.VALID
    with pytest.raises(ValueError):
        resolve_review(report, decision)
    report.checks = []
    with pytest.raises(ValueError):
        resolve_review(report, decision)


def test_noncanonical_output_and_unusable_recipe_return_invalid_not_exception(vtc_payload):
    from counterforge_contracts.mutations import digest
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate

    contract, seed, mutation = generated(vtc_payload)
    data = mutation.png + b"extraneous payload"
    tampered = replace(
        mutation,
        png=data,
        recipe=mutation.recipe.model_copy(update={"output_sha256": digest(data)}),
    )
    assert verify_candidate(contract, seed, tampered, VerificationPolicy()).verdict == "INVALID"
    contract, seed, mutation = generated(
        vtc_payload, family="brightness_shift", target_region="full_image"
    )
    tampered = replace(mutation, recipe=mutation.recipe.model_copy(update={"parameters": None}))
    assert verify_candidate(contract, seed, tampered, VerificationPolicy()).verdict == "INVALID"


def test_retained_phase04_specimens_have_honest_labels_and_recompute():
    import json
    from pathlib import Path
    from uuid import UUID

    from counterforge_contracts.mutations import (
        ApprovedMask,
        DiffMetrics,
        GeneratedMutation,
        MutationRecipe,
        SeedImage,
        digest,
    )
    from counterforge_contracts.responses import ValidationReport
    from counterforge_contracts.vtc import VisualTestContract
    from counterforge_verification.invariants import (
        VerificationPolicy,
        report_digest,
        verify_candidate,
    )
    from counterforge_verification.review import HumanReview, resolve_review

    root = Path(__file__).resolve().parents[1] / "fixtures" / "phase04"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["synthetic"] is True
    assert manifest["live_provider"] is False
    assert manifest["actual_human_review"] == "PENDING"
    contract = VisualTestContract.model_validate_json((root / "contract.json").read_bytes())
    seed_meta = manifest["seed"]
    seed = SeedImage(
        UUID(seed_meta["asset_id"]),
        UUID(seed_meta["seed_id"]),
        (root / "source.png").read_bytes(),
        seed_meta["sha256"],
    )
    masks = tuple(
        ApprovedMask(
            UUID(m["asset_id"]),
            seed.seed_id,
            seed.sha256,
            (root / m["file"]).read_bytes(),
            m["sha256"],
            "SYNTHETIC_TEST_MASK_APPROVAL",
        )
        for m in manifest["masks"]
    )
    for spec in manifest["specimens"]:
        for file, sha in spec["files"].items():
            assert digest((root / file).read_bytes()) == sha
        base = root / spec["name"]
        mutation = GeneratedMutation(
            (base / "image.png").read_bytes(),
            MutationRecipe.model_validate_json((base / "recipe.json").read_bytes()),
            (base / "union.png").read_bytes(),
            (base / "diff.png").read_bytes(),
            (base / "background.png").read_bytes(),
            masks,
            DiffMetrics(**json.loads((base / "metrics.json").read_text())),
        )
        report = verify_candidate(contract, seed, mutation, VerificationPolicy())
        retained = ValidationReport.model_validate_json(
            (base / "automatic-report.json").read_bytes()
        )
        assert report == retained
        assert report.report_sha256 == report_digest(report)
        assert report.verdict == spec["expected_automatic_verdict"]
        if (base / "simulated-review.json").exists():
            decision = HumanReview(**json.loads((base / "simulated-review.json").read_text()))
            resolved = resolve_review(report, decision)
            assert resolved.verdict == spec["expected_after_simulated_review"]
            assert resolved == ValidationReport.model_validate_json(
                (base / "simulated-report.json").read_bytes()
            )
    assert {s["name"] for s in manifest["specimens"]} >= {
        "accepted-after-simulated-review",
        "object-removal",
        "ambiguous-visibility",
        "seam-only-control",
    }


@pytest.mark.parametrize(
    "role", ["png", "background_png", "protected_union_png", "diff_png", "all"]
)
def test_retained_raster_recompression_is_not_a_structural_failure(vtc_payload, role):
    import io

    from counterforge_contracts.mutations import digest
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate

    contract, seed, mutation = generated(vtc_payload)
    changes = {}
    hashes = {}
    for name, key in [
        ("png", "output_sha256"),
        ("background_png", "background_sha256"),
        ("protected_union_png", "protected_union_sha256"),
        ("diff_png", "diff_sha256"),
    ]:
        if role not in (name, "all"):
            continue
        image = Image.open(io.BytesIO(getattr(mutation, name)))
        image.load()
        output = io.BytesIO()
        image.save(output, "PNG", compress_level=6)
        changes[name] = output.getvalue()
        hashes[key] = digest(output.getvalue())
    changed = replace(mutation, **changes, recipe=mutation.recipe.model_copy(update=hashes))
    report = verify_candidate(contract, seed, changed, VerificationPolicy())
    assert all(
        c.status == "PASS"
        for c in report.checks
        if c.code in {"MEDIA", "PROVENANCE", "RECIPE", "MASKS", "GEOMETRY", "PROTECTED_PIXELS"}
    ), report
    assert report.verdict == "REVIEW_REQUIRED"


def test_destroyed_visibility_is_a_failed_human_gate(vtc_payload):
    from counterforge_verification.invariants import VerificationPolicy, verify_candidate
    from counterforge_verification.review import resolve_review

    contract, seed, mutation = generated(vtc_payload)
    report = verify_candidate(contract, seed, mutation, VerificationPolicy())
    reviewed = resolve_review(report, review(report, visibility="destroyed"))
    assert reviewed.verdict == "INVALID"
    assert check(reviewed, "HUMAN_VISIBILITY").status == "FAIL"
    with pytest.raises(ValueError):
        resolve_review(reviewed, review(reviewed, visibility="clear"))
