# Phase 10 — Release and Submission Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Prepare concrete release artifacts before any publication approval that is still required.

**Goal:** Release an inspectable, reproducible and honestly demonstrated CounterForge MVP with complete evidence and attribution.

**Architecture:** A frozen release revision ties code, fixture tests, live evidence, dataset/report hashes and a read-only demo together. Public materials describe actual behavior and preserve the distinction between recorded evidence, live calls and test fixtures.

**Tech stack:** Existing application/Compose, Git release tooling, secret/license checks, clean-environment verification and screen recording.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§17, 20–25; [PRD](../CounterForge_PRD_v1.0.md) §§3–4, 14–20. Acceptance: PRD-AT-09, PRD-AT-11–12; TS-AT-19–20 and final reconciliation of all launch gates.

**Prerequisite:** [Phase 09](plan-09.md). **Next:** [Phase 11](plan-11.md), only after release. **Source target:** November 4, 2026, 21:00 US Eastern / **November 5, 2026, 03:00 Europe/Madrid**; verify final organizer guidance before submission.

## Global constraints

- No invented model failures, benchmarks, author credits or completed submissions.
- Technical launch gates are TS-AT-01–16 and TS-AT-18–20; TS-AT-17 is strongly recommended and covered by the evaluation plan.
- The PRD's stronger discovery goal remains visible: if no real stable failure was found, disclose that unmet goal rather than treating fixture completion as fulfillment.
- Public code/media must be rights-cleared and contain no secrets or confidential inputs.
- Publishing, spending and submitting follow existing human authorization; when approval is missing, prepare the complete reviewable artifact and stop only that release action.

## Review focus

- Fresh checkout setup can reveal undocumented dependencies or private local paths.
- A video can accidentally imply a replay is live or a fixture is a real-model discovery.
- A failed regression assertion must be explained as a discovered bug, distinct from a broken runner.
- Public access checks must work without the owner's authenticated browser.
- Last-minute changes must not invalidate frozen reports or evidence hashes.

## Tasks

### Task 1: Reproducible release documentation and attribution

**Files:** Root README, LICENSE, ATTRIBUTION, `.env.example`, `docs/{architecture,api,security,evaluation,submission,limitations}.md` and release evidence index.

**Interfaces:** README links exact setup commands, sample data rights, model roles, paid-test gates, expected regression-test behavior and documented limitations. Release evidence index points to code revision, source specifications, test results, experiment IDs and report/bundle hashes.

- [ ] Write prerequisites and fresh-checkout setup with uv/npm lockfile commands, Compose migration/start order, fixture-only tests, private owner setup and optional bounded live execution.
- [ ] Document Meta's planner/generator/perception roles, independent validity review, provider-managed revisions and any shared planner/target model family. Do not claim binary weight pinning or formal safety proof.
- [ ] Inventory original work, preexisting code, dependencies, dataset/media rights and contributors. Select a compatible project license with the owner's rights/intent confirmed; do not invent ownership or redistribute restricted provider/private assets.
- [ ] Reconcile public sample assets and notices against the dataset manifest; sanitize release fixtures, screenshots, logs and archives.
- [ ] Verify links and local-run instructions from a clean temporary checkout/export without access to the development database or private attachment folder.

### Task 2: Complete acceptance and security evidence

**Files:** `docs/submission.md` launch matrix, machine-readable check summaries and release manifests.

**Interfaces:** Each `PRD-AT-*` and `TS-AT-*` row has status, evidence reference, run date/revision and any explicit deviation. The coverage index in [the roadmap](README.md) identifies phase ownership.

- [ ] Re-run lint/type/unit/contract/DB/browser/export gates on the frozen revision, plus the migration and restore checks needed after any late changes.
- [ ] Verify retained real-provider smoke evidence and primary finding audit. If a new paid confirmation is needed, use an authorized allowance and the same bounded accounting path.
- [ ] Run secret/dependency/license checks against staged release files, relevant history, public assets, ZIPs and frontend outputs. Correct findings before publishing and preserve the scanner result without exposing matched secret values.
- [ ] Reconcile accepted/rejected counts, cost categories, confirmation denominators and evaluation conclusions to source records. Explicitly list unmet launch/discovery goals and their user impact.
- [ ] Confirm no P2 feature is required to finish release and no fixture or uncertain candidate contributes to genuine stable findings.

### Task 3: Accurate three-minute demonstration

**Files:** Demo storyboard, sanitized capture assets, final video and fallback replay instructions.

**Interfaces:** Follow technical specification §21's timing: seed/contract, Spark/SAM/Image process, rejected candidate, real finding if available, runnable regression and actual metrics/limits. Archive experiment IDs and evidence hashes used in each scene.

- [ ] Prepare the real recorded run and clearly labeled replay fallback. Use a fixture segment only as an explicit pipeline test, never as a foundation-model weakness.
- [ ] Show independent validity evidence and fresh response counts. Qualify 3/3 as three trials under recorded settings, not universal reproducibility.
- [ ] Run the exported test on screen and explain expected correctness assertion failure against a broken target. Include a healthy fixture runner self-test in supporting evidence, not as a false real-model improvement claim.
- [ ] Record/rehearse the three-minute video, check legibility/audio/duration and inspect frames for keys, private images, browser URLs and secret-bearing terminal output.
- [ ] If no genuine failure exists, narrate live-search results and the labeled regression pipeline honestly; disclose the discovery limitation in both video and README.

### Task 4: Reviewable publication and submission

**Files:** Final release checklist, prepared repository/deployment settings and submission draft/evidence.

**Interfaces:** Final submission contains repository/product/video URLs, track, team attribution and factual feature/evidence summary. Public product is the sanitized read-only surface; owner controls remain private.

- [ ] Recheck final organizer rules, deadline, required form fields, permitted content and current eligibility against authoritative guidance; the source documents are not a substitute for final rules.
- [ ] Prepare rights-cleared repository content, bounded read-only deployment and accessible video. Reuse existing authorization; request any still-needed publication/submission approval only when the concrete artifacts are reviewable.
- [ ] After authorized publication, test repository/video/demo from a fresh unauthenticated session and verify no public paid controls. Record actual URLs and access results.
- [ ] Submit only with authorization and retain confirmation/time or a truthful pending/failed state. Do not mark a submission completed merely because a draft exists.
- [ ] Commit verified release documentation with `docs: prepare reproducible CounterForge release and submission evidence`; preserve the frozen release revision and final acceptance matrix.

## Exit criteria and evidence

- [ ] Fresh setup and required quality gates pass; all deviations are explicit and attributable.
- [ ] Rights/secret checks and model-use/ground-truth evidence are available for review.
- [ ] Public materials accurately distinguish genuine results, fixture tests and replay.
- [ ] Authorized publication/access/submission actions have receipts; unapproved or unavailable actions remain clearly pending.

## Handoff

Retain the release, evaluation and original evidence before beginning phase 11.
The post-MVP roadmap does not retroactively change the release's measured scope.
