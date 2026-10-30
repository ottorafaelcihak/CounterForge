# Phase 08 handoff — evaluation and independent audit

Phase 08 implements the frozen-dataset, matched-budget evaluation harness
and independent audit described in [plan 08](plan-08.md) and
[the evaluation guide](../evaluation.md). Code and methodology tests are
complete. Measured real-model evaluation remains blocked on two human
gates (below); nothing in this handoff claims dataset labels, rights, or
paid results that do not exist.

## Implemented

- **Dataset admission** (`evaluations/dataset.py`): exactly 8 development
  + 4 held-out cases, rights/reviewer/timestamps fail closed, source and
  normalized hashes verified against the registered normalization policy,
  duplicate image bytes across splits rejected, exclusions retained.
- **Protocol** (`evaluations/protocol.py`): three predeclared arms
  (`random_det`, `spark_det`, `full_system`), equal total target-call
  caps including baselines/confirmations, frozen PRNG seed, ranges,
  order alternation, global API/dollar/runtime allowances. Live runs
  need a separate hash-bound authorization.
- **Runner/adapter** (`evaluations/runner.py`, `adapter.py`): durable
  serial scheduling over the existing Service/worker only; all-arm
  baselines precede search; incomplete membership blocks workers;
  interruption retains run IDs, spend and `UNKNOWN_CHARGE` exposure.
- **Audit** (`evaluations/audit.py`, `packet.py`): reproducible
  stratified random sample (≤5+≤5 per family×strategy) separate from
  exhaustive targeted finding audits; `REVIEW_REQUIRED` held in a
  distinct pending population; append-only correction chains; verified
  blinded image packet with hash-bound media and a separate private
  strategy mapping. Human judgments remain pending — none invented.
- **Report** (`evaluations/report.py`): source reconciliation, truthful
  null denominators, censored first-discovery, spend categories, latency
  percentiles, observed model versions, generation/SAM failures, audit
  corrections/holds, and incomplete-comparison gating that withholds
  difference/ratio on partial snapshots. Bundles require all four files
  and regenerate the CSV for comparison.
- **Matched loops**: under a `FEEDBACK_V1` declaration, adaptive arms
  run `FEEDBACK_V1` and the random baseline runs the seeded `RANDOM_V1`
  loop (same caps/gates, no planner, no learned preference); reports
  name both explicit policies. `ONE_SHOT` keeps every arm one-shot.

## Pending human gates

1. **Dataset labels/CC0**: 12 proposal assets were prepared outside the
   checkout (`/tmp/counterforge-phase08-dataset`) and shown to the user;
   independent counts and CC0 approval are unanswered. They are not
   imported, labeled, or rights-asserted. Templates in
   `evaluations/manifests/` remain `PENDING`/`null` and fail closed.
2. **Paid budget**: no Phase 08 live-API allowance exists. The Phase 07
   allowance is not transferable. No provider calls were made.

## Verification

Unit and protocol tests cover differing invalid rates under equal caps,
three-fresh confirmation gating, provider-outage retention, rehashed-CSV
and missing-file bundle failures, blinded packet hash binding, and a
real Service/worker pause → API review → resume preserving experiment
IDs, baselines and costs. Exact commands and outcomes are recorded in
the completion report. Docker images copy `evaluations/` before
`uv sync`; `make evaluate` stays no-money by default and routes
`EVALUATE_ARGS` to other CLI commands.

Phase 09 consumes frozen reports and sanitized evidence; presentation
must not silently improve these figures.
