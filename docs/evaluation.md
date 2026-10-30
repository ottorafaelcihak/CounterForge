# Phase 08 evaluation

Phase 08 adds a frozen, matched-budget evaluation harness, an independent
human audit, and source-reconciled reports. It measures validity, failure
discovery, reproducibility and cost. It does **not** yet report measured
real-model results: no approved human dataset labels and no Phase 08 paid
budget exist. Fixture output exercises methodology only and is never
presented as a real-model finding.

## What exists

| Capability | Status |
| --- | --- |
| Fail-closed dataset admission (rights, reviewer, hashes, 8 dev / 4 held-out) | Implemented; no approved cases admitted |
| Frozen protocol (3 arms, equal caps, 3-fresh confirmations) | Implemented |
| Durable runner over existing Service/worker (no new provider path) | Implemented |
| Blinded audit sampling (≤5 accepted + ≤5 rejected per family×strategy) | Implemented |
| Verified blinded image packet (hash-bound media + task question/expected count, observed-count form, private mapping separate, exact-member verification) | Implemented |
| Source-reconciled JSON/CSV bundle with CSV regeneration check | Implemented |
| Incomplete-comparison gating (no lift on partial snapshots) | Implemented |
| Measured live evaluation (100 variants, ≥90% precision goals) | **Pending** human labels + paid approval |

## Arms and fairness

The primary comparison is `random_det` vs `spark_det`: both deterministic,
same `brightness_shift` family and `[0, 1]` strength range, same seed pool,
target settings, validity gates and confirmation policy. `full_system` adds
generative families and is reported separately as a generator ablation;
it never enters the primary difference/ratio.

Each arm receives the same total target-call cap per seed, including the
3 seed-baseline calls and fresh confirmation calls. Actual calls consumed,
valid screening calls, planner/Image/SAM usage and dollar/runtime spend are
reported separately from the cap. An arm that exhausts its cap on
confirmations is censored, not efficient.

Baseline eligibility is decided once per seed: if any matched baseline is
not 3× `CORRECT`, the seed is excluded from matched comparisons for all
arms and its spend is retained. Strategy order reverses on odd seeds to
reduce temporal-provider bias. Observed model versions are recorded per
observation; multiple observed versions are flagged as drift uncertainty.

## Running (no money by default)

```bash
make evaluate                                            # fixture methodology only
make evaluate EVALUATE_ARGS="verify-bundle /path/bundle" # verify a frozen bundle
.venv/bin/python -m evaluations.cli fixture --output /tmp/counterforge-phase08-fixture
.venv/bin/python -m evaluations.cli audit-export source.json --seed 17 --output out \
  --images /path/to/images --packet-dir out/packet
.venv/bin/python -m evaluations.cli report source.json --output bundle/
.venv/bin/python -m evaluations.cli verify-bundle bundle/
```

Live execution (`run --live`) additionally requires a frozen approved
dataset, a separate live authorization binding the protocol/dataset
hashes, and an in-memory key supplied by the coordinator. No Phase 08
live allowance currently exists, so no provider calls are authorized.
One authorization binds exactly one run in a deployment database: the
global guard aggregates usage across every experiment sharing that
authorization and measures runtime from the earliest run start, so
replaying an authorization in a new directory/run cannot regain the
paid envelope or reset the clock. Fixture runs stay scoped per run.

The declared benchmark policy defaults to `FEEDBACK_V1` (Phase 07
adaptive feedback). Under that declaration the adapter runs adaptive
arms as `FEEDBACK_V1` and the random baseline as the matched seeded
`RANDOM_V1` loop: same candidate/target-call caps, validity and human
review gates, with `random_plan(seed + slot)` each slot and no planner
calls or outcome-conditioned ranking. Reports record both explicit
policies (`search_policies`) and the matched-loop stop semantics.
Under `ONE_SHOT` every arm stays one-shot — one-shot outcomes are not
evidence about feedback-policy lift.

## Reports

Every rate carries numerator/denominator; zero denominators emit `null`,
never an invented percentage. Reports include eligible/excluded seeds,
proposals, generated/invalid/review-required/valid counts, valid screening
calls, all paid target calls by phase, suspected/stable/intermittent
findings, audited counts, spend split (actual/estimated/unknown/reserved),
latency percentiles, time/calls to first discovery with censored
no-discovery runs, observed model versions, generation/SAM failures, and
audit corrections/holds.

`primary_comparison_valid` is false when any matched group is missing an
arm, baseline-excluded, or non-terminal (including partial creation
snapshots), or when a primary arm was stopped by the shared global
envelope (`GLOBAL_EVALUATION_ENVELOPE_EXPIRED`: global API/dollar,
global runtime or live-authorization expiry — including expiry reached
after a human-review wait, since review waits consume the global wall
clock). Globally censored arms are listed in `globally_censored` and in
`failed_interrupted_excluded`. In all these cases difference/ratio are
withheld (`null`). An arm stopped only by its own declared local
target/candidate caps remains a legitimate matched completion.

Audit reporting separates independent-audit holds (`audit_summary.holds`,
counted only for automatic-stable findings that cleared every non-audit
gate) from `non_audit_drops` / `exclusion_drops` (automatic-stable
findings dropped by baseline exclusion, candidate/screen gates or other
non-audit eligibility), so exclusions are never presented as human
audit holds.

Audited precision uses only the stratified random accepted sample.
Showcased/stable targeted audits are reported separately and never enter
the precision denominator. A stable finding counts toward headline totals
only after an independent audit `PASS`; corrections append to the audit
chain and the original verdict is retained. Candidate review or audit
never changes the expected label.

## Pending human gates

1. Twelve independently human-labeled, redistribution-cleared seeds
   (8 development, 4 held-out). Proposal assets exist outside the
   checkout; counts and CC0 approval are unanswered. Templates under
   `evaluations/manifests/` stay `PENDING`/`null` and fail closed.
2. A Phase 08 paid budget/authorization. The Phase 07 allowance is not
   transferable.

Until both gates pass, claims are limited to methodology correctness.
The proposed goals of 100 variants and at least 90% audited validity
precision are targets, not results. No genuine stable failure is claimed
without actual evidence.

See [limitations](limitations.md) and the
[Phase 08 handoff](plans/phase-08-handoff.md).
