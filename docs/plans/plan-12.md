# Phase 12 — Offline Regression CI Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` with the required three-provider bridge. One editor at a time on `master` (default: Codex
> implements; Claude Code reviews the diff; Muse checks acceptance). No
> delegation to a second editor in the same checkout.
**Status: `IMPLEMENTED_OFFLINE_VERIFIED`** — The owner approved this plan and [the Phase 11 CI design](phase-11-ci-design.md) on 2026-11-01. Local implementation and verification are recorded in the [Phase 12 handoff](phase-12-handoff.md). The [GitHub master run](https://github.com/ottorafaelcihak/CounterForge/actions/runs/41322213155) passed all four controls after the authorized push; live CI remains unapproved.


**Goal:** Ship an offline-only GitHub Actions regression check (0 provider calls, $0 cap) where a healthy fixture passes, a broken fixture fails as a regression, and tamper/infrastructure failures are execution errors.

**Architecture:** One new trusted module, `packages/regression/counterforge_regression/ci.py` (run isolated as `python -I -m counterforge_regression.ci`), verifies a bundle's reviewed outer SHA-256, delegates to trusted `bundle.verify_bundle`, safely extracts validated members to an immutable owned-temp snapshot outside the checkout, and invokes trusted installed `standalone.run` in a sanitized child shim emitting bounded protocol JSON — never code/tests/requirements from the bundle. Structured `CaseResult`/`SuiteReport` JSON drives exits `0/1/2`. The first workflow is a fixed `PIPELINE_SELF_TEST` suite over the committed retained fixture in `tests/fixtures/regression_ci/` (regenerated/byte-compared to the design §3 baseline pin `e91352ad…`, 8,234 bytes, before committing it; use the retained bytes as source of truth, and follow the design §3 separately reviewed re-pin procedure for legitimate encoding/dependency changes).

**Tech stack:** Existing Python 3.12 / `uv --locked`, `pytest`, `counterforge_regression` (`bundle.verify_bundle`, `standalone.run`, `RegressionManifest`), GitHub Actions (Linux hosted). No new dependencies.

**Spec:** [PRD](../CounterForge_PRD_v1.0.md) FR-20; [technical specification](../CounterForge_Technical_Specification_v1.0.md) §§1.3, 7, 9.5, 20.1, 23; [design](phase-11-ci-design.md); [Plan 11](plan-11.md) Workstream A. Sources (root-verified 2026-10-30): [secure use](https://docs.github.com/en/actions/reference/security/secure-use), [trigger events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

## Global constraints
- Offline only: no `MODEL_API_KEY` or any secret reference, no live mode,
  `provider_calls=0`, `cost_usd=0` in every report; live CI deferred (§8 of the design). No `pull_request_target`/`workflow_run`/schedule triggers.
- Never execute bundle code/tests/requirements, even if self-hashed. Outer
  reviewed SHA is identity; inner hashes alone are not authenticity.
- Do not modify `standalone.py`, the manifest schema, or bundle format
  `1.0.0`. Existing `make verify-export` must remain green.
- Bounds: catalog ≤10 entries; ZIP ≤100 MiB / ≤40 members (existing
  `verify_bundle`); wrapper child 30 s per bundle; CI job 10 min; concurrency group per workflow/ref with `cancel-in-progress: ${{ github.event_name == 'pull_request' }}` (master push/manual running receipts are not canceled); `permissions: contents: read`; full-40-SHA action pins verified at implementation, never invented.
- Reports contain no raw responses, media, env values, absolute paths, or
  tracebacks; bounded ≤16 KiB; fixed reason-code enum only.
- Infrastructure / provider-`UNKNOWN` ⇒ `EXECUTION_ERROR` (exit 2), never
  a correctness regression. No user-supplied expected-exit masking.

## Review focus
- Untrusted PR-head code/catalog must never replace the base-SHA consumer inputs; Task 3 checks the checkout and dispatch boundary.
- Unsafe report destinations or oversized/malformed child output must yield safe execution errors, with no raw output fallback; Tasks 1–2 pin this behavior.
- Rehashed malicious runner: outer SHA + `verify_bundle` template equality
  must stop it before invocation; extraction must reject symlink/`..`.
- Sanitized child env: injected `MODEL_API_KEY` + live flags present in the
  parent must yield 0 dispatches and an offline result.
- Self-test honesty: CI green only if all four fixed controls match;
  consumer exit 1 always fails a genuine job.

### Task 1: Wrapper core (`counterforge_regression/ci.py` + retained fixture)
**Files:** Create `packages/regression/counterforge_regression/ci.py`; create `tests/fixtures/regression_ci/v1-fixture.zip` + `catalog.json` (only after regenerating `fixture_bundle()` and byte-comparing to the design §3 pin — a differing hash stops the task); create `tests/unit/test_ci_regression.py`.
**Interfaces:** Exactly design §3 (including `SuiteReport.selected_ids` and `reason_code` for empty-check/global failures): `ExpectedOutcome`, `CiCatalogEntry`, `CaseResult`, `SelfTestCheck`, `SuiteReport`, `Mode`, `Verdict`; `evaluate_bundle(zip_bytes, *, trusted_sha256, mode, fixture_count=None, case_id=None) -> CaseResult`, `parse_catalog(data, *, catalog_dir) -> tuple[CiCatalogEntry, ...]`, `matches_control(actual, expected) -> bool`, `run_catalog(case_ids=None) -> SuiteReport`, `main(argv=None) -> int`. CLI subcommands `case --case-id … --report …` / `suite [--case-id …] --report …` against the committed catalog only (no URL/live/user pin overrides). Child shim: same module re-invoked isolated, imports trusted `standalone.run`.
- [x] Write the healthy/broken/replay/outer-pin and report-protocol tests below first; run them and confirm the missing module/API fails.
- [x] Implement SHA pre-check → `verify_bundle` → member-by-member safe extraction to an immutable owned-temp snapshot outside the checkout (all verification before invocation) → `sys.executable -I -m counterforge_regression.ci` child shim with allowlisted env (drop `MODEL_*`, `*_API_KEY`, `*_TOKEN`, `*_SECRET`, `PYTHONPATH`, live flags; internal child snapshot/mode/count supplied through argv only, never environment variables), 30 s timeout, runner prints suppressed/capped.
- [x] Shim catches `AssertionError` specifically⇒protocol `REGRESSION`/`COUNT_MISMATCH` (exit 1); `standalone.ExecutionError`/unknown/tamper/any other exception⇒`EXECUTION_ERROR` (exit 2). Parent requires bounded protocol-JSON⇔exit agreement; malformed/truncated/missing protocol, timeout, or counterfeit child exit 1 without valid regression protocol⇒exit 2 (`CHILD_TIMEOUT`/`CHILD_EXECUTION_ERROR`/`UNKNOWN_CHILD_OUTPUT`/`RUNNER_EXECUTION_ERROR`). Never classify by exit-1/traceback substrings.
- [x] Catalog loader: reject >10 cases, duplicate `case_id` or conflicting pins for one path (identical path/SHA reuse is allowed), bad SHA/mode, wrong outer pin (even for a rehashed archive) ⇒ `CATALOG_INVALID`/`BUNDLE_SHA_MISMATCH`; `expectation` accepted only for the four fixed synthetic IDs; `flip_first_byte` variant only for `tampered` (immutable retained archive). Safe `.json` report path validation ⇒ `REPORT_PATH_UNSAFE`.
- [x] Tests assert: healthy fixture (count 5) ⇒ `CaseResult PASS`, exit 0; broken (4) ⇒ `REGRESSION`, exit 1, `expected_count=5, actual_count=4`; replay ⇒ `REGRESSION`, exit 1; tampered bundle ⇒ exit 2 before child invocation; outer-SHA mismatch ⇒ `BUNDLE_SHA_MISMATCH`, exit 2.
- [x] Run: `uv run --locked pytest tests/unit/test_ci_regression.py -q` and `make verify-export`.

### Task 2: Hostile-input and failure-taxonomy tests
**Files:** Extend `tests/unit/test_ci_regression.py` only.
**Interfaces:** Same Task 1 API; no new public surface.
- [x] Add the following negative tests, run to observe failure, then harden only the responsible wrapper boundary; keep the v1 runner unchanged.
- [x] Malicious ZIP cases ⇒ exit 2, child never invoked (assert via a
  sentinel/monkeypatched child): absolute/`..` member, symlink member, duplicate member, oversize (>100 MiB declared or >40 members), missing/duplicate-JSON-key catalog/protocol (manifest behavior remains the existing verifier's contract), rehashed bundle with modified `runner/target_adapter.py` (self-consistent sums, wrong outer SHA and also wrong-template/right-format variant) ⇒ `BUNDLE_INVALID`/`BUNDLE_SHA_MISMATCH`.
- [x] Unknown target output (a valid replay fixture with unparseable recorded raw answer, or a stubbed `standalone.ExecutionError`) ⇒ `EXECUTION_ERROR`, never `REGRESSION`; counterfeit child exiting 1 with missing/truncated/mismatched protocol ⇒ exit 2; stubbed child timeout ⇒ exit 2, `CHILD_TIMEOUT`, bounded JSON report with no traceback/path/env strings (assert absence).
- [x] Env hygiene: parent env seeded with `MODEL_API_KEY=dummy` and a live
  flag ⇒ child env contains neither; result remains offline (`provider_calls=0`); unsafe catalog/Python API inputs (URL path, missing file, short SHA, replay with fixture count), and CLI unknown scenario/live/pin override or report path = input/symlink ⇒ exit 2 with safe codes.
- [x] Catalog tests call the Task 1 `parse_catalog` validation seam with bytes and an owned temporary directory, without overriding workflow/CLI lookup: 11 entries or empty/duplicate-ID/conflicting-pin input ⇒ `CATALOG_INVALID`; 10 ordinary entries may share identical path/SHA and pass catalog validation; unknown selected IDs, non-control suite selections or an expectation/variant on a non-control ID fail closed. Unit tests use 10 ordinary catalog entries without expectations; suite execution remains restricted to four synthetic IDs.
- [x] Prove suite-global catalog/report/child errors return reason-coded JSON even with zero checks; direct replay/fixture execution errors are covered here (the four-control workflow itself does not exercise post-spawn execution errors). Assert the child environment is exactly `PATH`/temporary `HOME` and parameters use argv, never inherited env; re-hash extracted files and test write-mode restoration/cleanup on success and timeout.
- [x] Run: `uv run --locked pytest tests/unit/test_ci_regression.py -q`.

### Task 3: Self-test workflow (`.github/workflows/ci-regression.yml`)
**Files:** Create `.github/workflows/ci-regression.yml`; extend `tests/unit/test_ci_regression.py` with static workflow checks (in this same file; do not add a second test module).
**Interfaces:** Separate consumer workflow `CounterForge offline regression` on `push`/`pull_request` to `master` + `workflow_dispatch` (existing CI keeps testing PR-head source); job timeout 10 min; concurrency `${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`; `permissions: contents: read`; `runs-on: ubuntu-latest`. Trust root: PR ⇒ checkout/install trusted base SHA (`pull_request.base.sha`, base `master` only) with catalog+archive from that same base, never PR head; push/manual on `master` ⇒ owner-reviewed/`github.sha`; non-`master` manual ref denied by an early ref check. No dispatch inputs exist: always run all four controls. No input can override catalog/pins. Steps: checkout + Python/`uv` setup (full-SHA pins verified at implementation), `uv sync --locked`, run `python -I -m counterforge_regression.ci suite --report <tmp>/suite.json`, compare observed exits/verdicts to the fixed design-§6 table, render the bounded `SelfTestCheck.expected/actual/matched` JSON with a static inline Python reader, never raw bundle strings/child output. The base-SHA job is not evidence about a PR's proposed wrapper/catalog changes; the existing PR-head unit job plus code review provide that coverage, and the actual master run supplies integration evidence. Retain `suite.json` via a full-SHA-pinned official artifact action with an always-run upload step; upload that exact safe JSON only, no media, environment dumps or raw child output. Do not publish a summary if the report is invalid/missing. First `master` addition bootstraps CI; never claim old `e0efa` contains `ci.py`.
- [x] Write static workflow and four-control comparison tests first; confirm the absent workflow/comparator fails, then implement this workflow.
- [x] Assert statically in tests: no `secrets.`, no `MODEL_API_KEY`, no
  `pull_request_target`, no `workflow_run`, no `schedule:`; every `uses:` is a 40-hex SHA pin; `permissions: contents: read`; `timeout-minutes: 10`; concurrency group + the PR-only cancellation expression present; base-SHA (not PR-head) checkout for PRs, non-`master` manual ref denied, and no workflow input can override catalog/pins.
- [x] Assert behaviorally (local simulation of the four controls through
  `main()`): observed `(0,PASS,OK,5,5),(1,REGRESSION,COUNT_MISMATCH,5,4),(1,REGRESSION,COUNT_MISMATCH,5,4), (2,EXECUTION_ERROR,BUNDLE_SHA_MISMATCH,—,—)`; exit alone is insufficient — flipping any of exit/verdict/reason/counts makes the comparison helper fail, and a stubbed timeout/missing-module/report-failure (exit 2, other reason) must not satisfy the tamper control (test the comparison function directly).
- [x] Document in the workflow that genuine consumer runs use the `case` subcommand against a future owner-reviewed catalog addition (design §4 template comment); no remote fetch or pin-override step exists. Counterfeit child (exit 1 with mismatched/truncated protocol) ⇒ 2 is covered in Task 2.
- [x] Run: `uv run --locked pytest tests/unit/test_ci_regression.py -q`;
  `make verify-export`; `git diff --check`. Static policy checks do not claim full YAML validation; actual GitHub workflow syntax/execution receipts remain pending until an authorized push. Add no YAML parser dependency.

### Task 4: Compatibility, docs, and acceptance
**Files:** Create `docs/plans/phase-12-handoff.md`; update `docs/plans/phase-11-ci-design.md` (status line only if approved), `docs/plans/plan-11.md`, `docs/plans/README.md`; no source changes.
**Interfaces:** Roadmap records Phase 11 A implemented-offline; FR-20 live remains incomplete. Phase 10 snapshot metadata unchanged.
- [x] Compatibility: `uv run --locked pytest
  tests/integrations/test_export_runner.py tests/unit/test_regression_bundle.py -q` (incl. old-format bundle test) and `make verify-export` all pass unchanged.
- [x] Full local gate: `make lint`, `make typecheck`,
  `uv run --locked pytest tests/unit -q` pass; confirm no full DB/E2E run is required for this increment.
- [x] After an authorized push, inspect the actual workflow run and record its URL and four-control results in `docs/plans/phase-12-handoff.md`. Until then the remote receipt stays pending; canceled/skipped runs are never passing receipts. Master runs use PR-only cancellation so a later master event does not cancel a running receipt. Prove negative exits locally with the fixed controls; do not push a deliberately broken master commit. The case consumer exit 1 remains a failure regardless of a synthetic suite result.
- [x] Update Plan 11 Workstream A checkboxes that are actually satisfied,
  README phase table/FR-20 row, and design status; owner approval was recorded on 2026-11-01.

## Exit criteria
- [x] All Task 1–4 tests pass; `make verify-export` green; workflow green
  on `master` with the four fixed controls matching.
- [x] No secret, live call, or bundle-code execution path exists; reports
  prove `provider_calls=0`, `cost_usd=0`.
- [x] Owner approval of the design + this plan recorded before implementing this plan; live CI remains a separate future design, funding and implementation decision.
