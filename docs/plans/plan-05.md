# Phase 05 — Bounded Experiment Pipeline Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Paid execution must go through the durable accounting path in every stage.

**Goal:** Run the full seed-to-finding loop with honest result classification, controlled spending and safe restart behavior.

**Architecture:** Short RQ jobs carry IDs and advance one durable stage at a time. PostgreSQL locks, operation keys and checkpoints serialize an experiment; provider calls happen only after committed reservations. The browser follows stored events and snapshots rather than inferring success from activity.

**Tech stack:** RQ/Redis, PostgreSQL, FastAPI/SSE, normalized provider/target adapters, mutation/verifier packages, Next.js and Pytest/Playwright.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§4, 6, 8, 11.2, 14–15, 17–20; [PRD](../CounterForge_PRD_v1.0.md) FR-05, FR-09–11, FR-13–14. Acceptance: PRD-AT-02, PRD-AT-05–07, PRD-AT-10; TS-AT-02, TS-AT-06–08, TS-AT-11–13.

**Prerequisite:** [Phase 04](plan-04.md). **Next:** [Phase 06](plan-06.md). **Target:** October 30, 2026.

## Global constraints

- Baseline is three calls on the original normalized seed; all must be interpretable and correct to search.
- Screen each VALID candidate once. A wrong screening result creates SUSPECTED, then requires three **fresh** confirmations with identical retained bytes, prompt and settings.
- STABLE requires 3/3 incorrect interpretable confirmations and valid evidence; the screening call is excluded.
- Unknown/refusal/provider errors are not count errors. A partially completed confirmation cannot be called stable.
- All planner, segmentation, image, target, confirmation and recovery dispatches reserve budget first. Default provider concurrency is 1.

## Review focus

- Duplicate enqueues and crashes at intent/result boundaries cannot silently replay charged calls.
- Invalid and unreviewed candidates never reach the target queue.
- A correct baseline, screening and confirmation have distinct attempt identities.
- Pause/cancel drains in-flight calls and prevents the next dispatch.
- Limits persist across restart, including unresolved charges and elapsed wall-clock time.

## Tasks

### Task 1: Experiment starts, baseline and immutable invocation settings

**Files:** Experiment API/repositories, worker step handlers and `tests/integrations/test_experiment_start.py`, `test_seed_baseline.py`.

**Interfaces:** `POST /api/v1/experiments` takes `StartExperiment` and `Idempotency-Key`, returns `202 ExperimentRef`; `advance_experiment(experiment_id: UUID) -> None` is the bounded scheduler entry point. Snapshot VTC, target prompt/settings, adapter/parser/verifier/pricing versions and exact asset IDs/hashes.

- [ ] Test same-key/same-body retry => same ID; changed body => 409; cross-project target/contract => reject. Start and initial state event commit atomically before enqueue.
- [ ] Implement `DRAFT -> BASELINE_VALIDATING -> READY` only after three correct seed observations. Consume baseline calls from the same target-call and dollar budgets as the search.
- [ ] A wrong interpretable baseline becomes `SEED_INVALID_FOR_SEARCH` with a visible reason and no mutation calls. Unknown/error responses cannot establish readiness: preserve diagnostics and stop or pause for review under the normalized provider-error policy.
- [ ] Persist each baseline raw response, fingerprint and version metadata; locked contracts and later target edits cannot change the running snapshot.
- [ ] Verify start/baseline DB tests and TS-AT-11 idempotency; a failed baseline fixture causes zero planner/image/search calls.

### Task 2: Bounded search and independent candidate gating

**Files:** Worker scheduler/runner/steps, operation repositories and `tests/integrations/test_experiment_loop.py`.

**Interfaces:** The scheduler consumes phase 03's planner, phase 04's mutation/verifier and phase 02's reservations. API mode values remain `ADAPTIVE` and `RANDOM_BASELINE`; P0 `ADAPTIVE` records a one-shot Spark plan with strategy revision `spark-initial-v1`, upgraded by phase 07 rather than adding an unofficial mode.

- [ ] Write a fixture end-to-end test: correct seed, typed plan, one VALID candidate, one INVALID candidate, one REVIEW_REQUIRED candidate and a wrong screening reply on the valid one.
- [ ] Invoke an actual bounded Spark planning step for live P0 runs, validate proposals and cap work by remaining budget. A fallback is explicitly marked deterministic; it does not satisfy the real Spark evidence gate.
- [ ] Checkpoint plan, generation, validation and evaluation stages; commit generated image bytes before marking GENERATED. Queue target screening only after a final VALID decision.
- [ ] Leave uncertain candidates available for audited review and exclude them from confirmed metrics. When no runnable work remains and human action is needed, pause with an explicit review reason rather than spinning or calling providers again.
- [ ] Implement database runner ownership, heartbeats, operation-key deduplication, bounded queue capacity and the source state transitions. Append transition events in the same transaction as state updates.
- [ ] Verify duplicate enqueues produce a single screening operation; an invalid/review-only candidate produces no target request.

### Task 3: Confirmation classification and complete evidence

**Files:** Finding/reproduction service, confirmation routes and `tests/unit/test_reproduction.py`, `tests/integrations/test_confirmation.py`.

**Interfaces:** `confirm_finding(finding_id, confirmation_job_id) -> FindingView`; phase-tagged observations use `SCREENING` and `CONFIRMATION` separately. A normal confirm request continues/returns the existing canonical confirmation job, avoiding duplicate attempts; new version comparisons use phase 07 RETEST jobs.

- [ ] Test 3/3 incorrect => STABLE; `4,5,4` against expected 5 => INTERMITTENT with 2/3; 0/3 incorrect => NOT_REPRODUCED.
- [ ] Require all three fresh confirmation slots before a final interpretable classification. Conservative default: any unresolved UNKNOWN/PROVIDER_ERROR leaves REVIEW_REQUIRED with observed counts; never substitute screening or fabricated responses for a missing slot.
- [ ] Record every result, even correct/refused/error, with input hash, prompt fingerprint, model revision claim, timestamp, inference settings and sanitized raw asset.
- [ ] Before scheduling confirmations, ensure the complete batch fits remaining target-call/dollar/time allowance. If it cannot, retain SUSPECTED and the precise budget stop reason; do not claim reproduction.
- [ ] Require the final finding to remain VALID and expose a separate audited human demo-approval action/state in finding metadata. An overridden/invalidated validity decision removes confirmed/demo eligibility.
- [ ] Verify PRD-AT-06/07 and TS-AT-07/08 with fixture calls counted explicitly as one screen plus three confirmations.

### Task 4: Spend caps, pause/cancel and crash recovery

**Files:** Worker budget/recovery modules, control routes and `tests/integrations/test_worker_recovery.py`, `test_experiment_controls.py`.

**Interfaces:** §8 pause/resume/cancel routes return `202 ExperimentRef`; recovery reconciles persisted `run_operations`, `usage_events` and heartbeats, with no provider dispatch from readiness/health checks.

- [ ] Test crashes before send, after send but before response persistence, after image commit and after event commit. Reuse persisted completed results; only provably unsent/CPU work is safely retried.
- [ ] Quarantine an ambiguous sent request as UNKNOWN/UNKNOWN_CHARGE, retain conservative consumption and pause for operator resolution. Do not promise exactly-once provider billing when the provider offers no such guarantee.
- [ ] Enforce candidate, target-call, dollar and elapsed-runtime caps before every dispatch. Wall-clock runtime uses the persisted start time and includes paused time; resume after expiry exhausts the run instead of resetting the clock.
- [ ] Implement PAUSE_REQUESTED/CANCEL_REQUESTED: stop new calls, account for in-flight completion/timeout and reach PAUSED/CANCELLED. Resume only PAUSED runs within the remaining limits.
- [ ] Test concurrent reservations at the final budget slot, queue overflow, cancellation during confirmation and restart with unresolved spend. If actual usage exceeds a conservative estimate, record it honestly and stop further scheduling.
- [ ] Verify `make test-db` plus worker integration tests against real PostgreSQL/Redis and fixture provider transport.

### Task 5: Initial cockpit and real bounded search

**Files:** Experiment snapshot/events/mutations/findings routes, web cockpit/timeline and browser integration tests.

**Interfaces:** SSE uses `Last-Event-ID`, monotonic persisted event IDs and approximately 15-second keepalives. Browser reconnect re-fetches the snapshot; private SSE remains authenticated without putting tokens in URLs.

- [ ] Display state, candidate verdicts, reasons, reserved/spent/unresolved amounts, target calls and limits; add pause/resume/cancel and links to review/evidence.
- [ ] Test event replay, duplicate delivery, reconnect snapshot, baseline rejection, review pause and budget exhaustion with generated client types.
- [ ] Run one authorized bounded real search using approved seed/masks, real Spark planning, SAM and Muse Image evidence. Save outcomes even if no model failure is found.
- [ ] Verify fixture browser flow and all worker gates, then record live experiment/evidence IDs and usage separately. Commit with `feat: run durable bounded experiments and failure confirmations`.

## Exit criteria and evidence

- [ ] Fixture loop proves validity gating, baseline rules and all reproduction classifications.
- [ ] Recovery/control/cap tests pass and no scheduling path bypasses accounting.
- [ ] Real Meta-powered search evidence is stored; any missing entitlement or undiscovered failure remains an explicit unmet goal.
- [ ] Preserve experiment IDs, snapshots, operation/usage records and human-review evidence for export.

## Handoff

Phase 06 consumes stable, human-reviewed findings and retained observations. It
must not treat an incomplete or fixture-only discovery as a genuine AI failure.
