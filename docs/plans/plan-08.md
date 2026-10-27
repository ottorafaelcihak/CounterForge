# Phase 08 — Evaluation and Independent Audit Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Predeclare the evaluation and report unsuccessful runs alongside successes.

**Goal:** Measure validity, failure discovery, reproducibility and cost using a rights-cleared frozen dataset and fair strategy comparisons.

**Architecture:** Versioned manifests bind labels, rights, image hashes and split membership. A bounded harness schedules the existing worker policies against fixed target settings. An independent audit records human judgments and produces machine-readable results plus a readable report.

**Tech stack:** Python/Pydantic, existing experiment APIs, PostgreSQL, JSON/CSV result exports, Pytest and static analytical figures if needed.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§10.3, 11.4, 15–17, 20, 24; [PRD](../CounterForge_PRD_v1.0.md) FR-17, §§4, 13, 15. Acceptance: TS-AT-17; supports PRD/technical rights and public-results requirements.

**Prerequisite:** [Phase 07](plan-07.md). **Next:** [Phase 09](plan-09.md). **Target:** November 2, 2026.

## Global constraints

- Prepare at least 12 independently human-labeled seeds: 8 development and 4 frozen held-out.
- Share seed pool, target settings, resolution, transform ranges, validity gates and confirmation policy across compared strategies.
- Match total target-call allowances; count seed-baseline and fresh-confirmation calls, and show actual calls consumed separately.
- Proposed goals of 100 variants and at least 90% audited validity precision are targets, not claimed results.
- No prompt/policy/threshold tuning on held-out images; report excluded baseline-failing seeds and all generation expenses.

## Review focus

- Split leakage and duplicate assets can invalidate the held-out comparison.
- Different invalid rates or confirmation overhead can make a nominally equal comparison unfair.
- Auditing only successful findings inflates accepted-mutation precision.
- Zero denominators and undiscovered failures must remain visible rather than becoming fabricated percentages.
- A seam artifact or manual correction can explain results and must be disclosed.

## Tasks

### Task 1: Licensed, labeled and frozen dataset

**Files:** `evaluations/seeds/`, `evaluations/manifests/`, dataset validation tooling and `tests/unit/test_dataset_manifest.py`.

**Interfaces:** Each manifest entry contains case ID, source/license or permission, source/normalized hashes, dimensions, verified count, reviewer/time, ambiguity notes, split and contract/policy version. Public assets must be redistribution-cleared.

- [ ] Validate missing rights/reviewer, changed hashes, duplicate image membership, split overlap and ambiguous labels before admitting a case.
- [ ] Assemble varied count/size/scene/background cases within the 1–16 instance MVP contract. Have a human verify visibility independently of generator metadata.
- [ ] Assign 8 development and 4 held-out IDs and freeze a manifest hash before final tuning/evaluation. Store any excluded cases with reasons instead of deleting their history.
- [ ] Restrict tuning to development cases. Held-out evaluation may inspect outcomes for reporting only; a later policy change starts a separately labeled evaluation revision.
- [ ] Verify dataset checks and licensing records without invoking providers.

### Task 2: Predeclared matched-budget harness

**Files:** Evaluation runner/config under `evaluations/`, strategy manifests and `tests/integrations/test_evaluation_protocol.py`.

**Interfaces:** `make evaluate` consumes an explicit manifest/policy/target revision, PRNG seeds and finite authorized query/dollar/runtime allowances; writes run IDs and status even when interrupted. Default dry-run/fixture mode does not spend money.

- [ ] Predeclare random deterministic, Spark-plan deterministic and full-system comparisons using the same task-verification standard. The adaptive/random primary comparison uses the same available families and strengths; report generator-enabled ablations separately when their available transformations differ.
- [ ] Run the unchanged-seed baseline for each target/seed before searching. When baseline cannot establish correctness, exclude that seed consistently from matched comparisons and report its observed results.
- [ ] Allocate the same total target-call cap per strategy/seed, including baseline and confirmations; show valid screening calls separately. Planner, Image and SAM usage also consume dollar/runtime allowances and appear in total cost.
- [ ] Freeze PRNG seeds, allowed parameter ranges, confirmation policy, stop rules and strategy order; alternate strategy order across seeds to reduce temporal-provider bias.
- [ ] Include a seam-only control and a SAM ablation only when independent human verification still establishes validity. Do not call unverified ablation outputs equally trustworthy findings.
- [ ] Test equal budgets with different invalid rates, confirmations near the cap, provider outages, no discoveries and resumed reports. Verify harness tests before any authorized live evaluation.

### Task 3: Human audit and evidence eligibility

**Files:** Audit selection/decision manifests, human-review UI/export and `tests/unit/test_audit_sampling.py`.

**Interfaces:** Audit entries reference candidate/evidence hashes, prior automatic verdict, reviewer/time, independent judgment and rationale; strategy identity is hidden during review where practical.

- [ ] Audit every showcased finding and every stable finding entering the headline evidence set. Separately select a reproducible stratified random sample of accepted and rejected candidates by family/strategy.
- [ ] Use up to five randomly selected accepted and five rejected candidates per family/strategy stratum, or all available if fewer; retain the sampling seed and actual sample sizes. This is an initial small-sample policy, not a statistical-power guarantee.
- [ ] Compute accepted-mutation precision over the random accepted sample; report any additional targeted finding audits separately to avoid selection bias.
- [ ] Revoke or hold findings whose ground truth no longer survives review; retain both original verdict and correction audit. The evaluator never changes the expected label to rescue a discovery.
- [ ] Verify that disproven and unreviewed candidates cannot enter confirmed headline totals and sampling can be reproduced from the manifest.

### Task 4: Measured results and qualified conclusions

**Files:** Machine-readable evaluation report, `docs/evaluation.md`, `docs/limitations.md` and metric formula tests.

**Interfaces:** Report fields include eligible/excluded seeds, proposals, generated/invalid/review-required/valid images, valid screening calls, all paid target calls, suspected/stable/intermittent findings, audited counts, spend categories and latency percentiles.

- [ ] Test formulas on fixed fixtures, including zero denominators. Emit null/not-applicable with numerator/denominator rather than Infinity or an invented zero-success percentage.
- [ ] Report `stable findings / valid screening calls`, also per 100 and per total paid target calls; report `stable / suspected`, audited precision, invalid/generated rate, time/calls to first discovery and censored no-discovery runs.
- [ ] Compare equal-budget counts and cost; report adaptive difference and a ratio only when the random denominator is nonzero. Make no superiority claim if results do not support it.
- [ ] Separate actual usage, conservative estimates and unresolved charges. Describe model-version uncertainty, sample sizes, seam controls, manual intervention, audit corrections and generation/SAM failure modes.
- [ ] Run authorized evaluation only within the explicit allowance; freeze raw result and report hashes. Verify calculation tests, report-to-source reconciliation and sample bundle checks, then commit with `test: evaluate CounterForge with frozen labels and matched budgets`.

## Exit criteria and evidence

- [ ] Dataset rights, labels, hashes and split freeze are inspectable.
- [ ] Compared methods share declared budgets and validity rules; deviations and exclusions are visible.
- [ ] Every reported metric reconciles to experiment/observation/audit records with actual denominators.
- [ ] Evaluation goals achieved or missed are stated honestly; no fixture results appear as real-model findings.

## Handoff

Phase 09 consumes frozen reports and sanitized evidence for display. Phase 10
uses the same measured figures; presentation must not silently improve them.
