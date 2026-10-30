# Phase 07 handoff — adaptive search, refinement and version retests

Phase 07 adds optional feedback search, independently reviewed reduction probes,
and repeatable target-B retests. P0 one-shot search remains the default. The
[policy record](phase-07-policy.json) freezes engineering defaults for Phase 08;
these are not measured probabilities or an optimal policy. A real development run
showed Spark consuming an actual prior outcome. No genuine model failure, live
regression export or improvement over random was demonstrated.

## Feedback search

Choose `FEEDBACK_V1` with `ADAPTIVE` and a recorded random seed in project setup.
`ONE_SHOT` preserves existing requests, including old idempotency fingerprints.
Random exploration owns attempt slots 0, 5, 10 and so on, reserving
`ceil(0.20 * max_candidates)` slots without lending them to exploitation.
Duplicate images and rejected selections consume bounded attempts. Every proposal
still passes contract, affordability, validity and accounting gates.

The versioned selector uses Beta(1,1) validity and failure estimates, an exploration
term `1/sqrt(1+family_trials)`, lambda 0.1 and epsilon 0.000001. Unknown outcomes stay
visible and do not become incorrect/correct binary samples. Family and proposal
order break ties. Cost components use conservative server estimates. Sanitized
history retains at most ten rows and 16 KiB; planner input is bounded to 32 KiB.
No private URLs, credentials, raw provider envelopes or media enter history.

Each batch retains the actual immutable input history, remaining limits,
policy/template revisions, random allocation and proposed/selected/rejected ranking.
A settled planner receipt is reused after a crash before checkpointing. Lease
recovery is scoped to the current feedback slot: completed earlier planner calls
are not treated as unresolved, and a current sealed receipt with its frozen input
context is decoded without another send. Ambiguous current sends remain blocked. Invalid or
refused planner output keeps the labeled deterministic fallback. Cockpit details
show these records, including the actual outcomes supplied to each batch.

## Bounded refinement

An eligible stable finding on a COMPLETED source experiment can request
`POST /api/v1/findings/{id}/minimize` with `Idempotency-Key` and `probe_limit` 1–8.
Only one unfinished refinement owns a source. Disabled or mismatched provider modes
stop before generating a child or consuming candidate capacity; fixture labels
remain bound to the source. Original candidate, target-call,
dollar and wall-clock ceilings remain binding; the clock does not restart.

Brightness probes move between the failing strength and identity 0.5 while keeping
the original source, direction, curve and RNG. Deterministic rectangle clutter
removes recorded layers using the same random prefix. Generated backgrounds without
removable elements return an explicit unsupported result without regeneration.
Existing recipe schemas and hashes remain compatible.

Every new child retains parent/job lineage and pauses for independent semantic and
visibility review. Review does not resume automatically. Return to the finding and
explicitly resume the job; only VALID candidates can dispatch. An independently
INVALID probe can finish without dispatch. A separate screen and three fresh wrong
confirmations are required before replacing the retained best. Passing neighbors,
intermittence, unknown results, aliases and exhausted budgets remain evidence.
The source finding is preserved. Results describe the simplest tested stable
variant, never a proven global minimum. A paused job can be explicitly stopped.
Child exports retain parent mutation, parent finding and minimization-job provenance.

## Exact-input target-B retests

Verify a retained bundle before starting a fresh retest from the regression library
or finding export card. `POST /api/v1/regressions/{export_id}/retest` takes an
`Idempotency-Key`, an enabled curated target in the same project and explicit
`limits` for calls, dollars and runtime. Each new job gets a separate accounting
allowance bounded by the deployment ceiling. It reuses the bundle's exact input,
prompt and human answer; target B's sampling/settings are frozen separately.

Additive migration `0006_phase07_support` adds job identity to RETEST observations,
separate uniqueness from ordinary attempts, refinement lineage and source fencing.
It follows the already-existing migration chain; the plan's illustrative `0003`
name would collide. The supplied SQL baseline is unchanged. Retests do not edit
historical A observations, source budgets or the original sealed bundle. Repeated
jobs can test the same input without observation collisions.

Three correct replies mean FIXED; three wrong mean PERSISTENT; three interpretable
mixed replies mean INTERMITTENT. Any unknown, provider error, partial execution or
budget stop is INCONCLUSIVE. The UI separates completed/3 coverage, interpretable
correctness, uncertainty and provider errors, alongside timestamps, hashes and A/B
settings. Configured revision labels do not prove a separately pinned model;
unchanged target settings and provider-managed versions are disclosed. Simulated exports cannot be retested as real-provider comparisons; both the API
and worker enforce this boundary before reservation. Ambiguous
paid work remains charged conservatively and is never blindly retried after recovery.

Strategy comparison reports exclude refinement children and RETEST observations from
search discovery, validity, phase and call denominators. Followup costs remain
separate as `refinement_usage` and `retest_usage`; `source_usage` and
`source_target_calls_consumed` retain the full original cap exposure. The report
therefore cannot gain discoveries by simplifying one existing finding, or hide
followup expenses in search yield.

## Authorized live demonstration

See the [sanitized retained result](phase-07-live-results.json). The original
25-call/USD 15 allowance used 4 calls/USD 0.005416 before expiring while waiting for
review. It was canceled without extending its clock. The user separately approved
a fresh bounded restart using only the remaining 21 calls/USD 14.99 and approved
the exact candidate SHA `859299b0…`. New bytes would still require a new review.

The restart used 6 calls/USD 0.009385: three correct seed baselines, one supporting
SAM call (zero detections, INCOMPLETE), one correct screen and one Spark planning
call. The regenerated image matched the approved SHA exactly. The second batch
retained author META_SPARK without fallback and consumed the actual VALID/CORRECT
candidate history, recipe, cost and remaining limits. Execution stopped before
second-candidate generation. Phase 07 total: **10 calls/USD 0.014801 actual, zero
unresolved charges**. These are ledger usage costs, not a provider invoice audit.

Both runs retain ignored local declarations, exact artifacts, ledgers and database
backups. Independent restore checks verified table counts and all 18 original/21
restart artifact hashes. The exact 105-file reviewed runtime was also retained
locally with every manifest hash verified. The live run used a frozen, reviewed Task 1 runtime while
the backend worker completed independent changes on a separate disposable database.
No credentials or raw private graphs are committed.

## Verification and remaining gates

Final verification against the integrated source:

| Gate | Result |
| --- | --- |
| Offline unit and contract suites | 347 passed, 7.12 seconds |
| Complete disposable PostgreSQL integration suite | 247 passed, 407.14 seconds before the final review corrections; includes worker fences/recovery, migration, minimization, retest and comparison isolation |
| Final affected worker suite after review corrections | 76 passed, 186.72 seconds; all P0 worker, feedback and minimization integration checks |
| Complete real-process fixture browser suite | 10 passed, 108.48 seconds |
| Final affected retest/comparison integration checks | 20 passed, 66.57 seconds |
| Final retest browser check after provenance guard | 1 passed, 24.07 seconds |
| Python static checks | Ruff check/format pass, 183 files; mypy passes 98 source files |
| Web and packaging | Typecheck, ESLint, production build, generated-contract check, quiet Compose validation and API/worker image builds pass |

Unit/contract sockets stayed disabled; database/browser checks allowed only
explicit disposable loopback services. Test human reviews and target replies were
simulated and labeled. Independent review also covered the completed implementation;
root added regressions for fixture-to-live promotion and comparison contamination,
then ran the full database gate. Claude subsequently found a feedback-slot lease
recovery bug and premature minimization capacity consumption in unavailable modes.
Both were fixed with five regressions (4 failed before correction, all 5 passed
afterward); Muse independently reviewed the corrections and approved them without
blockers. Affected worker and browser gates were rerun after these corrections. No live minimization or
model-B retest was performed: those paths are verified with fixtures. Fixtures test
the full evidence and export machinery; they do not establish a real model weakness.
Usable SAM masks on this illustration, automatic threshold calibration, a genuine
stable failure and live export remain outstanding. Phase 08 must use held-out data,
matched budgets and the frozen policy before making any search-lift claim.

Codex (`gpt-6.1-sol`) implemented backend behavior, Muse (`muse-wonder-1.0`)
reviewed feedback/acceptance seams, and Claude Code (`claude-opus-5`) reviewed
correctness and the completed diff. The coordinator integrated
presentation, ran the bounded live test and verified the final workspace.
