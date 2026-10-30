# Phase 07 — Adaptive Search and Finding Refinement Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Preserve the working P0 pipeline while adding optional search improvements.

**Goal:** Use observed outcomes to improve subsequent proposals, find simpler validated failures and compare regression behavior across configured model versions.

**Architecture:** Spark receives sanitized hypothesis/outcome history and remaining limits. A reproducible server-side selector validates and ranks proposals while reserving exploration capacity. Minimization creates child mutations; retests create distinct durable jobs without editing historic observations.

**Tech stack:** Existing planner/worker/verification/regression packages, PostgreSQL/Alembic, Pydantic, NumPy PRNG, FastAPI and Pytest.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§8, 11, 12, 15–17, 20; [PRD](../CounterForge_PRD_v1.0.md) FR-15–16, FR-19, §21. Supports TS-AT-17's later comparison.

**Prerequisite:** [Phase 06](plan-06.md). **Next:** [Phase 08](plan-08.md). **Target:** October 31–November 1, 2026.

## Global constraints

- Adaptive planning, minimization and retests cannot change a locked expected answer or bypass validity/accounting.
- Reserve at least 20% of candidate slots for recorded random exploration.
- Reuse exact retained image assets; stochastic provider generations are not assumed reproducible from a seed.
- A simplified stable failure needs independent validation and three fresh confirmations.
- Version retests use authorized configurations of the existing curated target registry; adding external providers is phase 11.

## Review focus

- A planner receives rejection/unknown outcomes without being allowed to rewrite them.
- Exploitation cannot spend the random exploration allocation.
- Non-monotonic or intermittent failures cannot produce a false global minimum claim.
- Repeated retests cannot collide with historical observation uniqueness constraints.
- Source ground truth and model-A evidence remain intact after model-B tests.

## Tasks

### Task 1: Feedback-driven Spark proposals and deterministic ranking

**Files:** Planner history/selection modules, worker adaptive loop and `tests/unit/test_adaptive_policy.py`, `tests/contracts/test_planner_history.py`.

**Interfaces:** Preserve `ExperimentPlanner.plan(contract, history, remaining_budget) -> list[MutationPlan]`; history contains hypotheses, recipe/family/strength, validity/rejection reason, target outcome and cost summaries. Store policy/template revisions and proposed versus selected rankings.

- [x] Test a second planner batch receiving prior accepted, rejected, correct, incorrect and unknown results with remaining budget; no raw secrets or unnecessary private media enter history.
- [x] Add bounded history windows and server validation of every new proposal. Continue using the labeled P0 fallback when Spark refuses or cannot supply valid output.
- [x] Implement the §11.1 objective with a versioned initial policy: Beta(1,1)-smoothed per-family validity/failure estimates, exploration term `1/sqrt(1+family_trials)`, `lambda=0.1` and `epsilon=0.000001`. Treat these as engineering defaults to evaluate, not measured probabilities or claimed optimal settings.
- [x] Reserve `ceil(0.20 * max_candidates)` slots for seeded random proposals; do not reallocate unused exploration slots to exploitation. Tie-break ranking deterministically by family and proposal order; filter unaffordable operations first.
- [x] Test small budgets, zero observations, all-invalid proposals, exhausted exploration and repeatable rankings. Verify `make test-unit` and `make test-contract`.

### Task 2: Bounded minimization with boundary evidence

**Files:** Minimization worker step/service, child-mutation recipes and `tests/integrations/test_minimization.py`.

**Interfaces:** `minimize_finding(finding_id, probe_limit=8) -> MinimizationResult`; source experiment limits remain the ceiling. Child mutations carry `parent_mutation_id`; result retains the best validated stable child, tested neighbors, stop reason and consumed budget.

- [x] Test a monotonic brightness fixture, an intermittent fixture, invalid intermediate candidates, budget exhaustion and a simpler neighbor that no longer fails.
- [x] For brightness, binary-search between the failing recipe and its no-change strength, preserving direction/curve parameters. Validate, screen and freshly confirm each candidate before calling it stable.
- [x] For clutter, remove recorded deterministic clutter layers or reduce retained background detail through a recorded deterministic operation; do not request an impossible identical regeneration from Muse Image. If a recipe has no meaningful removable element, report unsupported minimization for that case.
- [x] Bound probes by the lesser of eight and the remaining affordable work. Preserve the original finding on invalid/unknown/intermittent outcomes or when no reduction succeeds.
- [x] Retain passing neighbors as boundary evidence and describe the result as the simplest tested stable variant, not a proven global minimum.
- [x] Verify minimization integration tests and exported-child provenance; no probe dispatch can bypass phase 05's reservation path.

### Task 3: Repeatable version-B retest jobs

**Files:** Retest route/service, additive Alembic revision `0003_retest_attempts`, observation repositories and `tests/integrations/test_retest.py`.

**Interfaces:** `POST /api/v1/regressions/{export_id}/retest` consumes the frozen bundle and authorized `target_config_id`, returns `202 JobRef`. Job status/result reports correctness, fresh confirmation counts, target revision and comparison with the historic finding.

- [x] Add `model_observations.retest_job_id` referencing background jobs; require it for RETEST observations. Preserve non-RETEST uniqueness and replace the mutation attempt index with separate uniqueness for ordinary attempts and `(retest_job_id, mutation_id, attempt_no)` for RETEST. Test migration and two independent retests of the same image.
- [x] Enforce project ownership, checksum verification, idempotency and new-job request fingerprinting. The retest target must already be curated and authorized.
- [x] Use the bundle's exact input/prompt/expected answer and snapshot the version-B settings separately. Run three fresh retest calls under an explicit bounded allowance; keep correctness/uncertainty counts and the conservative confirmation policy.
- [x] Never rewrite historical target-A observations or mark an image invalid because model B answered differently. Report fixed, persistent, intermittent or inconclusive behavior with denominators and provider-version limitations.
- [x] Verify changed-target/idempotency conflicts, unknown outputs, missing credentials, budget stop and repeated completed retests without observation collisions.

### Task 4: P1 evidence and regression-suite presentation

**Files:** Cockpit hypothesis history, finding minimization panel, regression retest controls and P1 acceptance tests.

**Interfaces:** Generated API types present adaptive rankings, child lineage and retest job results; no independent frontend enums.

- [x] Add visible proposed/selected/fallback planning history, best-tested reduction, boundary neighbors and model-A/B comparison without changing original evidence labels.
- [x] Run fixture browser checks for no improvement, unsupported minimization, unknown retest and budget stop; existing P0 browser path stays green.
- [x] For authorized live development runs, retain evidence of a subsequent Spark plan consuming actual prior outcomes. Do not claim lift over random until phase 08 measures it.
- [x] Verify unit, contract, DB, worker and relevant browser suites; commit with `feat: add adaptive search minimization and version retests`.

## Exit criteria and evidence

- [x] Tests prove feedback consumption, deterministic selection and reserved exploration.
- [x] Reduced findings retain validity/confirmation evidence; unsupported or unsuccessful minimization is explicit.
- [x] Repeated model-version retests preserve history and are isolated by job identity.
- [x] Freeze the policy revision and allowed strengths before held-out evaluation.

## Handoff

Phase 08 compares the frozen adaptive policy against random and one-shot planning
with the same validity rules and target-call allowances. Improvements remain
hypotheses until measured.

Implementation evidence, policy freeze, authorized live usage and remaining real-
provider gates are recorded in the [Phase 07 handoff](phase-07-handoff.md).
