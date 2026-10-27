# Phase 03 — Meta Integrations and Mask Review Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Keep fixture checks and authorized live checks separate.

**Goal:** Connect the specified provider capabilities through bounded adapters and let the owner inspect, correct and approve seed masks.

**Architecture:** Shared transport owns wire protocols, usage and uncertain outcomes. Provider adapters normalize results into application types. Seed segmentation runs as a durable background job; the web app reads same-origin assets and submits audited mask decisions.

**Tech stack:** Meta-compatible Python SDK, official SAM parser, httpx, Pydantic, Pillow/NumPy, FastAPI/RQ, Next.js and Playwright.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§5, 7.2–9, 13–15, 17–18, 23; [PRD](../CounterForge_PRD_v1.0.md) FR-04, FR-06, §9. Acceptance: TS-AT-03, TS-AT-06, TS-AT-18 and PRD-AT-09.

**Prerequisite:** [Phase 02](plan-02.md). **Next:** [Phase 04](plan-04.md). **Target:** October 28, 2026.

## Global constraints

- Source defaults: `https://api.meta.ai/v1`, `muse-spark-1.3`, `muse-image-1.0`, `sam-3.1`; verify entitlement, SDK/parser compatibility, documented payloads and current pricing before live integration.
- `MODEL_API_KEY` remains on the server. Never pass it in an RQ payload, URL, browser DTO or recording.
- Provider calls must use phase 02 reservations. An ambiguous post-send timeout is not a free retry.
- SAM's streamed special-token grammar requires the official parser; do not JSON-decode raw segmentation text.
- Human count remains authoritative; mask mismatch cannot rewrite it.

## Review focus

- Refused, incomplete and malformed output does not masquerade as usable data.
- Signed provider image URLs cannot turn the adapter into an arbitrary fetcher.
- Stream truncation, empty masks and wrong dimensions block automatic acceptance.
- Mask corrections preserve prior evidence and reset approval for the new revision.
- A successful fixture handshake cannot be reported as a live integration.

## Tasks

### Task 1: Verified provider transport and accounting

**Files:** `packages/meta_adapters/counterforge_meta/{transport,usage}.py`, central `pricing.toml`, live-smoke script and `tests/contracts/test_meta_transport.py`.

**Interfaces:** All adapters receive server settings and an operation reservation. Normalize `ProviderResult` metadata, request IDs, latency, sanitized raw-response asset and pricing revision. Outcomes distinguish rate limit, refusal, incomplete, known failure and unknown charge.

- [ ] Capture official documentation URLs, verification date, selected SDK/parser versions, accessible model IDs and pricing upper bounds in an integration record. Do not treat SDK construction as access verification.
- [ ] Test connection failure before send, 429 with Retry-After, post-send timeout, invalid response shape and a redacted error body.
- [ ] Implement finite connect/read deadlines, bounded backoff with jitter for documented safe retries and no blind retry after an ambiguous dispatched request. Settle or quarantine every reservation.
- [ ] Cap decoded response sizes and sanitize metadata; omit base64 media, keys, headers and signed URLs from logs and public outputs.
- [ ] Verify `make test-contract`: safe retries are bounded, ambiguous operations retain their reservation and all provider errors use normalized codes.

### Task 2: Planner, scene generator and target adapters

**Files:** Meta Spark/Image modules, `packages/targets/counterforge_targets/meta_spark.py`, `packages/verification/counterforge_verification/counting.py` and adapter/parser tests.

**Interfaces:** `ExperimentPlanner.plan(...) -> list[MutationPlan]`; `SceneGenerator.create_scene(...) -> bytes`; `TargetModel.evaluate(...) -> TargetResult`. `parse_count(raw_text: str) -> int | None` accepts a single integer or exactly controlled `{"count": int}` JSON; it never asks another model to repair the answer.

- [ ] Test Spark's valid proposal, invalid family/strength, refused output and malformed schema. Use the generated small plan schema with the provider's documented structured-output endpoint, then independently validate it against the VTC.
- [ ] Implement a labeled deterministic fallback for invalid/unavailable Spark proposals. Keep the raw proposal/hash and template version; do not call it Spark-authored output.
- [ ] Test Muse Image bytes/base64 and signed-URL responses. Permit only verified provider hosts, TLS, bounded content size and revalidated redirect destinations; persist bytes immediately and omit remote URLs from evidence.
- [ ] Test target replies `"5"`, `" 5\n"` and `{"count":5}` as interpretable; reject verbose prose, multiple numbers, floats, booleans, empty strings and cutoff/refused output as unknown/error rather than a count mismatch.
- [ ] Keep `META_SPARK` prompts/settings separate from the planner, preserve provider-managed revision claims honestly and label `LOCAL_FIXTURE` as synthetic test behavior.
- [ ] Verify `make test-unit` and `make test-contract`; credentials and paid endpoints are absent from the default execution path.

### Task 3: SAM normalization and audited mask revisions

**Files:** SAM adapter, seed/mask routes and repositories, web mask-viewer/editor, `tests/contracts/test_sam.py` and `tests/integrations/test_mask_review.py`.

**Interfaces:** `Segmenter.segment(...) -> SegmentationResult(objects, outcome, diagnostics)`, with each object containing the ID, bbox, mask asset ID and optional confidence from §9.3. Use §8 segmentation/approval routes and add `POST /api/v1/seeds/{seed_id}/masks/{mask_id}/revisions` with multipart mask PNG and required notes.

- [ ] Test complete/incomplete streams, wrong-sized or nonbinary/empty masks, missing instances and incorrect instance count.
- [ ] Parse only completed results with the official parser, validate geometry and store every derived mask as an immutable artifact. Persist incomplete diagnostics and require review instead of auto-approval.
- [ ] Implement durable segmentation/target-check jobs using phase 02 job and usage records. Review output through job-status and mask-read routes.
- [ ] Build image/contour preview, overlay toggle, count display, brush-based correction, retry and approve actions. A revision creates a new asset, updates the current draft association, clears its approval and records actor, notes, old/new hashes and geometry.
- [ ] Preserve old asset approval evidence in append-only audit history so locked VTC snapshots continue resolving the exact reviewed revision. New contracts cannot borrow approval from an older mask asset.
- [ ] Require acknowledgement of a segmentation/count mismatch and human-corrected distinct masks before completing the seed setup. Never modify `expected.value` automatically.
- [ ] Verify the mask-review browser flow and API audit tests, including TS-AT-03's four detections for a human count of five.

### Task 4: Owner setup and gated live smoke evidence

**Files:** Project/seed/target/VTC setup pages, `scripts/smoke_meta.sh`, seed manifest and `tests/e2e/test_seed_setup` browser specs.

**Interfaces:** Generated API types drive upload, model selection, contract forms and job polling. Live script requires `RUN_META_LIVE_TESTS=1` and explicit finite call, dollar and runtime limits; an absent allowance blocks dispatch.

- [ ] Finish the minimal setup journey: create project, upload rights-cleared seed, name label reviewer, configure curated target, review masks and create the VTC.
- [ ] Prepare at least one development seed with image hash, source rights and verified count; do not use held-out images for integration tuning.
- [ ] When live usage is authorized within the agreed allowance, run one tiny Spark plan, SAM segmentation, Muse Image scene and target handshake. Use central reservations and concurrency default 1; store sanitized outputs and actual/estimated usage.
- [ ] Record missing entitlement, unsupported SKU or quota failure as a blocked live gate with an honest reason. Fixture success remains useful but does not satisfy TS-AT-18.
- [ ] Verify `make test-contract` and the fixture-backed seed setup browser test. Run `make smoke-meta` only with the explicit live-test gate; retain provider request/evidence IDs.
- [ ] Commit verified work with `feat: integrate bounded Meta adapters and mask review`.

## Exit criteria and evidence

- [ ] All adapter fixtures, strict parsing, error boundaries and setup/review browser tests pass.
- [ ] One approved seed has reviewed mask assets and a locked-version-ready VTC.
- [ ] Live integration evidence exists for available authorized providers, or each outstanding gate is clearly marked unmet.
- [ ] Store model/SDK/parser/pricing verification records, smoke usage and evidence IDs for phases 04–06.

## Handoff

Phase 04 consumes approved masks, immutable source assets and the normalized scene
generator. Phase 05 reuses the strict target parser and performs the full three-call
seed baseline; this phase's single target handshake is not that baseline.
