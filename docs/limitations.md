# Limitations

Honest limits of the current CounterForge evidence. Fixture success does
not establish a real model weakness.

## Evaluation (Phase 08)

- **No measured real-model evaluation yet.** No approved 12-case human
  dataset (labels/rights pending) and no Phase 08 paid authorization
  exist. All Phase 08 numbers produced so far are methodology-fixture
  ledgers with provenance `METHODOLOGY_FIXTURE` / `LOCAL_FIXTURE`.
- **Goals are not results.** 100 variants and ≥90% audited validity
  precision are proposed targets. Neither is achieved or claimed.
- **No genuine stable failure is claimed** without three fresh incorrect
  confirmations plus an independent human audit `PASS` on real-provider
  evidence.
- **Small audit samples.** Up to five accepted and five rejected
  candidates per family×strategy stratum is an initial policy, not a
  statistical-power guarantee. Precision denominators stay small.
- **Provider drift.** The target is provider-managed; sequential arms can
  observe different model versions. Reports record observed versions and
  flag multiplicity, but cannot eliminate drift.
- **Seam and SAM controls not run.** Seam-only and SAM ablations require
  independent human verification and are reported `NOT_RUN`.
- **Baseline exclusions shrink the matched set.** Seeds whose baseline
  cannot establish correctness are excluded consistently and reported.
  The matched comparison is conservative and all-or-nothing: any
  excluded seed (or missing, non-terminal or globally censored primary
  arm) withholds the pooled difference/ratio for the whole report,
  rather than comparing over only the remaining eligible seeds.
- **Human-review waits consume the global wall clock.** The global
  runtime is measured from the earliest declaration start and is never
  reset (anti-replay), so time parked in `WAITING_REVIEW` counts against
  `max_runtime_seconds` and the live authorization expiry. Size the
  allowance for expected review latency before buying a live budget.
- **Global envelope censoring is not a matched completion.** When the
  shared global API/dollar/runtime/authorization envelope stops an arm
  (`GLOBAL_EVALUATION_ENVELOPE_EXPIRED`), that arm is reported as
  globally censored and the matched difference/ratio is withheld. Only
  an arm's own declared local caps produce a legitimate matched
  `BUDGET_EXHAUSTED` completion under equal caps.
- **Censored runs are not failures or successes.** No-discovery, paused,
  interrupted and budget-exhausted runs are retained with explicit
  censoring; they do not become zero-percent rates.
- **Audit holds are independent-audit holds only.** Automatic-stable
  findings dropped by baseline exclusion or other non-audit gates are
  reported separately as non-audit/exclusion drops, never as findings
  held by the independent human audit.
- **Manual intervention is disclosed, not hidden.** Any correction,
  audit hold or superseding judgment remains in the append-only audit
  chain alongside the original verdict.

## Platform

- Single-image object counting within the 1–16 instance MVP contract.
- Automatic threshold calibration remains outstanding (Phase 07).
- Public demo publication belongs to Phase 10; nothing here authorizes
  paid calls, public deployment or submission.
