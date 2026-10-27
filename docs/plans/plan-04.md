# Phase 04 — Controlled Mutations and Independent Verification Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Treat validity as a gate, not a score to optimize around.

**Goal:** Generate controlled counting variations and attach independent, inspectable evidence that their ground truth remains valid.

**Architecture:** A registry dispatches allowed deterministic or Muse Image background recipes. Human-approved foreground pixels are composited over backgrounds. A versioned verifier combines media, provenance, geometry, pixel, instance and visibility checks into a three-way validity decision.

**Tech stack:** Pillow, OpenCV, NumPy, Meta adapters, artifact store, Pydantic and Pytest.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§5, 9.3–10, 14, 17; [PRD](../CounterForge_PRD_v1.0.md) FR-07–08, §7. Acceptance: PRD-AT-03–05; TS-AT-04–05.

**Prerequisite:** [Phase 03](plan-03.md). **Next:** [Phase 05](plan-05.md). **Target:** October 29, 2026.

## Global constraints

- P0 families are exactly `background_replacement`, `background_clutter`, `brightness_shift`.
- Generative changes target `background_only`; no intentional protected-object addition, removal, repaint or occlusion.
- Each output retains immutable image bytes, input hashes, recipe, software versions and mask references.
- Mandatory FAIL means INVALID; otherwise mandatory UNKNOWN/unreviewed evidence means REVIEW_REQUIRED; only all required checks passing means VALID.
- SAM and Spark outputs alone cannot certify a human-established count.

## Review focus

- An added target-class object in a background invalidates a preserved-foreground composite.
- Thin contours, seams and feathering can change visible objects despite a high overlap score.
- Brightness clipping can destroy visibility without altering instance geometry.
- Wrong masks or missing provenance must block target testing.
- Manual review must not silently overrule a failed invariant.

## Tasks

### Task 1: Deterministic transforms and versioned recipes

**Files:** `packages/mutations/counterforge_mutations/{deterministic,registry}.py`, recipe schemas and `tests/unit/test_mutations.py`.

**Interfaces:** `generate_mutation(seed_asset, reviewed_masks, plan, rng_seed) -> GeneratedMutation`; result contains image asset, recipe, family, strength, generator and intermediate evidence asset IDs. Only typed, VTC-approved plans enter the registry.

- [ ] Write tests for strength bounds, disallowed families, deterministic replay from retained source bytes, object-preserving background replacement and image-dimension consistency.
- [ ] Implement deterministic brightness and background transforms. Record the exact strength-to-parameter mapping, color space, clipping, PRNG seed and implementation version in each recipe.
- [ ] Use a conservative versioned verifier policy: exact interior foreground preservation for compositing, a separately measured contour ring and human review for uncalibrated visibility or semantic uncertainty. Calibration on reviewed development examples must precede automatic acceptance; do not invent universal safe thresholds.
- [ ] Write normalized PNG results atomically through the artifact store; never regenerate an asset merely to view or export it.
- [ ] Verify `make test-unit`: identical deterministic inputs/recipe produce identical normalized output hashes; invalid plans never invoke a provider.

### Task 2: Muse backgrounds and source-object compositing

**Files:** Compositor module, scene-generation job integration and `tests/unit/test_compositor.py`.

**Interfaces:** `composite_background(source_bytes, background_bytes, approved_masks, blend_policy) -> CompositeResult`; output includes composite, protected union, interior/ring differences and intermediate hashes. Scene generation uses the phase 03 adapter behind a reservation.

- [ ] Test a changed background with preserved interiors, foreground removal, dimensions mismatch and a synthetic sixth target object in the background.
- [ ] Generate contexts using Muse Image, then composite the original approved object pixels rather than trusting a generative edit to preserve them.
- [ ] Keep antialiasing within the recorded narrow contour policy, measure it separately and flag visibility uncertainty. Store the generated background, mask, final composite and diff.
- [ ] Add a seam-only control fixture using source-like backgrounds; reserve its comparative evaluation for phase 08.
- [ ] Verify preserved-pixel assertions and fixture composites. An optional authorized live background generation must retain its real usage/evidence and pass the same verifier.

### Task 3: Independent verifier and uncertainty policy

**Files:** `packages/verification/counterforge_verification/{invariants,review}.py`, validation repositories and `tests/unit/test_invariants.py`.

**Interfaces:** `verify_candidate(contract, seed, mutation, policy) -> ValidationReport`, containing verdict, verifier/policy version, mandatory check outcomes, reason codes and evidence asset IDs. This service never calls the target to decide image validity.

- [ ] Write a truth-table test covering mandatory FAIL/UNKNOWN/PASS combinations; averaging favorable scores must not override failure.
- [ ] Validate decoding/size, source/output SHA, allowed family, recipe completeness, mask ownership/approval, geometry and protected pixel changes.
- [ ] Use SAM's candidate segmentation as supporting evidence. A mismatch or possible extra same-class object leads to INVALID for a verified violation or REVIEW_REQUIRED for uncertainty; require independent human resolution before VALID.
- [ ] Route clipped, hidden, blended or ambiguous objects to REVIEW_REQUIRED even if mask overlap passes. Evidence includes check values and policy version, not just a boolean.
- [ ] Add `POST /api/v1/mutations/{id}/review` handling with actor, note, previous verdict and reviewed evidence hashes. Overriding an automatic INVALID requires a verified ground-truth audit resolving the failed check; a bare checkbox is rejected.
- [ ] Verify removal => INVALID, ambiguous occlusion => REVIEW_REQUIRED, valid background => VALID after required review, and added target object => never automatically accepted.

### Task 4: Visible validation evidence and end-of-phase specimens

**Files:** Candidate read DTOs, reusable validation/evidence components and integration tests for stored reports.

**Interfaces:** Later gallery/cockpit consumes candidate assets, `ValidationReport` and review status through API v1; provider URLs and internal keys remain private.

- [ ] Expose original, candidate, masks, diff and named check outcomes through authenticated artifact IDs.
- [ ] Build a minimal evidence view and reviewer action for phase 05 integration; show INVALID and REVIEW_REQUIRED distinctly with explicit reasons.
- [ ] Retain at least one accepted background composite, one known rejected object-removal case and one ambiguous-visibility case as labeled fixtures; identify live-provider specimens separately.
- [ ] Verify `make test-unit`, `make test-contract` and DB round-trip tests for reports and audit evidence. Commit with `feat: generate and independently verify controlled image mutations`.

## Exit criteria and evidence

- [ ] Each family produces recorded, immutable recipes and inspectable outputs.
- [ ] Known invalid and uncertain images cannot pass the verifier automatically.
- [ ] One complete VALID evidence package and rejected/uncertain counterexamples are retained with policy versions.
- [ ] Threshold calibration evidence and semantic-review limitations are documented; held-out examples are not used for tuning.

## Handoff

Phase 05 consumes generated assets and final validity decisions. It may call the
target only for candidates whose current evidence-backed decision is VALID;
manual review never silently changes the contract answer.
