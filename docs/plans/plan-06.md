# Phase 06 — Regression Export and MVP Gate Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Verify exported runner behavior in a clean environment.

**Goal:** Turn finding evidence into a portable correctness test and complete the first reviewable MVP with a small random comparison.

**Architecture:** Idempotent export jobs collect immutable assets and sanitized provenance into a ZIP using vetted static templates. A standalone runner verifies hashes, invokes the configured target and asserts the expected count. Evidence screens explain validity, reproduction and cost.

**Tech stack:** Python ZIP/hash utilities, Pydantic manifest, Pytest, target adapter templates, FastAPI, Next.js and Playwright.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§8, 11.4–13, 15, 17, 20, 24; [PRD](../CounterForge_PRD_v1.0.md) FR-12–13, FR-17, §14. Acceptance: PRD-AT-08–09; TS-AT-09–10, TS-AT-14 and an initial TS-AT-17 comparison.

**Prerequisite:** [Phase 05](plan-05.md). **Next:** [Phase 07](plan-07.md). **Target:** October 30, 2026; P0 freeze.

## Global constraints

- Regression format version is `1.0.0`; assert `parsed_count == expected.value`.
- A broken model causes an assertion failure; a fixed/healthy model passes. A transport/parser failure is an execution error, not evidence of an incorrect count.
- No provider keys, private signed URLs, machine paths or generated model code in bundles.
- Replay uses retained observations and is visibly labeled recorded replay, never live reproduction.
- A small random baseline is required here; full dataset evaluation is phase 08.

## Review focus

- Hashes must cover the exact evaluated image, not a regenerated near-copy.
- Pending/retried export requests cannot create inconsistent bundles.
- Partial confirmations and invalidated findings cannot produce eligible stable exports.
- The runner must fail for the right reason, with expected/actual output.
- Comparison denominators include baseline/confirmation costs and invalid-generation expenses.

## Tasks

### Task 1: Manifest and safe deterministic bundle construction

**Files:** `packages/regression/counterforge_regression/{manifest,bundle,runner_template}.py`, export job handler and `tests/unit/test_regression_bundle.py`.

**Interfaces:** `build_bundle(finding_id, export_job_id) -> ExportRef`; `RegressionManifest` follows technical specification §12.1 with version, ground truth, target/settings, validation/reviewer, screening/fresh confirmations, recipe, hashes and recorded timestamp.

- [ ] Write tests for required files, tampered/missing evidence, unsafe member names, secrets in metadata and a finding whose validity was revoked.
- [ ] Package manifest, instructions, normalized original, failing image, protected mask, comparison, target responses, validity report, vetted runner/tests, pinned requirements and `sha256sums.txt` as in §12.
- [ ] Retain original upload bytes as an additional `original-source.<ext>` when different from the normalized original, and identify both hashes in the manifest. The regression input must be the exact retained bytes used in confirmation.
- [ ] Generate manifests after collecting evidence and hash every packaged file except the checksum file itself, avoiding recursive self-hashes. Use only safe relative paths and static code templates.
- [ ] Sanitize raw response records, allowlist export metadata and scan the complete ZIP for secrets/private references before marking it ready.
- [ ] Verify `make test-unit` and bundle integrity tests; damaged evidence fails export rather than silently being omitted.

### Task 2: Portable correctness runner and labeled replay

**Files:** Runner templates, standalone export self-test harness and `tests/integrations/test_export_runner.py`.

**Interfaces:** Live execution reads provider credentials from environment, uses the manifest's model/prompt/options and returns a correctness assertion. `--replay-recorded` uses stored observations only and prints its offline/replay provenance.

- [ ] Write runner integration tests with a healthy fixture returning 5 and broken fixture returning 4 for expected 5.
- [ ] Verify checksums before any model invocation; reject unknown format versions and unsafe paths. Use the same strict count-parser semantics as the product.
- [ ] Implement a clean extracted-bundle run with no imports from the repository or access to its database. Missing credentials/unknown output produce explicit execution diagnostics, not a fabricated mismatch.
- [ ] Implement recorded replay without network access. Preserve the historic incorrect result; do not invert the test into asserting that the broken response is correct.
- [ ] Verify `make verify-export`: healthy-target Pytest exits 0; broken-target Pytest exits 1 with an expected/actual assertion; checksum tampering blocks target invocation. The verification harness regards this pair of results as success.

### Task 3: Idempotent export API and visible finding evidence

**Files:** Finding/export/read routes, web finding detail and regression library pages, browser export tests.

**Interfaces:** Use §8's finding confirmation/export and export status/download routes. A queued ExportRef is backed by phase 02's background job; READY resolves to verified regression assets. Finding detail/read and regression list use the DTOs frozen in phase 02.

- [ ] Test same-key export retry => same job/export ID; changed request => conflict; unauthorized/private downloads => deny.
- [ ] Require current VALID evidence, stable completed confirmations and human review for showcased findings. Fixture exports remain available for tests but prominently identify `LOCAL_FIXTURE` throughout.
- [ ] Show original/variant/diff/masks, validity check evidence, fresh responses with denominators, prompt/settings/model identity, reviewer and qualified reproducibility statement.
- [ ] Add status polling, checksum/download instructions, runner usage and error/retry states. Never signal ready before ZIP/manifest are committed and verified.
- [ ] Verify fixture-backed Playwright finding-to-download flow and authorization checks; scan browser network payloads for secret/private storage fields.

### Task 4: Minimal random baseline and P0 evidence review

**Files:** Reproducible random strategy in worker/plans, a small comparison manifest and initial evaluation report.

**Interfaces:** `RANDOM_BASELINE` uses a recorded PRNG seed, the same available transform ranges, input resolution, target configuration, validity checks and confirmation rule. Store a strategy revision and total/valid screening/confirmation call counters separately.

- [ ] Add a seeded random family/strength selector and compare it with the P0 one-shot Spark strategy on the same approved development seed pool under equal target-call caps.
- [ ] Predeclare small caps using the authorized remaining allowance; report zero discoveries, rejected candidates, stopped/unfinished runs and all generation/planning costs honestly.
- [ ] Review P0 end-to-end evidence: correct baseline, real Spark planning, SAM masks, Muse Image variation, independent review, target observations, confirmations if found and a runnable export.
- [ ] Run a genuine finding's bundle in a clean environment when such a finding exists. Retain the expected failing assertion and the live model metadata; disclose provider nondeterminism or changed behavior.
- [ ] If no genuine stable failure exists, keep the pipeline's fixture demonstration labeled and record the discovery/export-live goal as unmet; do not declare that goal passed.
- [ ] Verify `make verify-export`, fixture E2E and the initial comparison report, then commit with `feat: export verified regression bundles and complete MVP evidence`.

## Exit criteria and evidence

- [ ] Healthy/broken runner self-tests and tamper/secret/path tests pass in a clean extracted environment.
- [ ] Evidence and spend are visible with working authenticated export/download.
- [ ] A small matched-budget random comparison is retained with exact denominators.
- [ ] Record the P0 gate outcome: genuine reproduced failure/export demonstrated, or a precise unmet-goal/deviation statement. Targets are not guaranteed results.

## Handoff

Phase 07 builds on a frozen, testable MVP and versioned bundles. Keep a labeled
replay fallback without weakening real-model evidence or validity requirements.
