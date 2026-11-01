# Phase 11 Workstream A — Regression CI Design (Option A, Offline-Only)

**Status: `IMPLEMENTED_OFFLINE_VERIFIED`** — The owner approved this design and [Plan 12](plan-12.md) on 2026-11-01. See the [Phase 12 handoff](phase-12-handoff.md) for local verification. The [GitHub master run](https://github.com/ottorafaelcihak/CounterForge/actions/runs/41322213155) passed all four offline controls; live CI requires a separate decision.

**Goal:** Run reviewed CounterForge regression bundles in GitHub Actions with zero provider calls, so a healthy target passes, a broken target fails as a correctness regression, and infrastructure failure is never misreported as a regression.

**Spec:** [PRD](../CounterForge_PRD_v1.0.md) FR-20; [technical specification](../CounterForge_Technical_Specification_v1.0.md) §§1.3, 7, 9.5, 20.1, 23; [Phase 11 roadmap](plan-11.md) Workstream A; [Phase 10 handoff](phase-10-handoff.md) (frozen runtime `8029ff2`, Phase 10 commit `e0efa1e`; public release gates still pending — unchanged by this design).

## 1. First increment: offline only
- Live CI is **deferred, not implemented**: 0 model calls, $0 cost cap, no
  `MODEL_API_KEY`, no secret references anywhere in the new workflow.
- Repository: the CF owner's repo, default branch `master`.
- Triggers: `push` to `master`, `pull_request` targeting `master`, and manual
  `workflow_dispatch` (offline self-test only). No `pull_request_target`, no `workflow_run`, no schedule, no arbitrary remote-artifact fetch.
- Runner: Linux hosted ephemeral runner (`ubuntu-latest`), job timeout
  **10 minutes**, `permissions: contents: read` (least privilege).
- Concurrency: group per workflow + ref
  (`${{ github.workflow }}-${{ github.ref }}`), with `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`: PR runs may cancel older runs; master push/manual runs do not cancel a running master receipt.
- Actions pinned by **full 40-char SHA**, selected and verified at
  implementation time from the action's official release — never invented here. Pin updates are reviewed diffs, not auto-trust.
- Sources (verified by root, 2026-10-30): [secure
  use](https://docs.github.com/en/actions/reference/security/secure-use), [triggering events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments). They support least-privilege tokens, full-SHA pinning, no secrets for fork PRs, the requirement for a dispatch workflow file to exist on the default branch (our policy separately rejects runs selecting another ref), and environment reviewer gates for the future live step.

## 2. Core invariant
The **outer, trusted, reviewed catalog SHA-256 of the whole ZIP** gives a bundle its identity. Inner `sha256sums.txt` / manifest hashes alone are not authenticity: anyone can rehash a malicious bundle. Therefore:
1. The wrapper requires an explicit expected outer SHA-256 supplied from a
   reviewed catalog (§4), or an explicitly trusted hash passed to the Python API. Mismatch ⇒ `EXECUTION_ERROR` (`BUNDLE_SHA_MISMATCH`) before any extraction.
2. The wrapper then calls the **current trusted**
   `counterforge_regression.bundle.verify_bundle(data)` (existing bounds: ≤100 MiB ZIP, ≤40 members, ≤100 MiB total uncompressed, regular files only, safe names, no duplicates, runner members byte-equal to trusted `static_members()`).
3. Only validated members are extracted, member-by-member (never blind
   `extractall`), into an owned temp directory **outside the checkout**; absolute/`..`/symlink members ⇒ `EXECUTION_ERROR` before invocation.
4. **Never execute code, tests, or `requirements.txt` from the bundle**, even
   if self-hashed. Execution uses only the trusted installed `counterforge_regression.standalone.run(root, mode, fixture_count)`, imported from the checkout's locked install, run in a child process.
No change to `standalone.py`, the manifest schema, or the v1 bundle format (`format_version 1.0.0`). Existing verify-export/old-format compatibility tests must keep passing (§7).

## 3. Wrapper: `counterforge_regression/ci.py` (proposed, one new module)
The installed package module runs isolated as `python -I -m counterforge_regression.ci`, so neither checkout-cwd nor ZIP code is executed; bundle `runner/`/`tests/` are never imported. Its trusted child shim suppresses/caps all runner prints and catches vetted `standalone.run` exceptions specifically: `AssertionError`⇒`REGRESSION`/`COUNT_MISMATCH`, `ExecutionError`/unknown/tamper/timeout/any other exception⇒`EXECUTION_ERROR`. Never map child exit 1 or traceback substrings to a regression. The shim emits one bounded fixed-schema protocol JSON (`verdict`, `reason_code`, `expected_count`, `actual_count`, `mode`, `provenance`); the parent requires protocol⇔exit agreement (`0/1/2`) — malformed/truncated/missing protocol⇒exit 2. The workflow has no dispatch inputs and always runs all four controls. Internal child parameters use an argv list (never a shell), as specified below; they never travel through environment variables.
**CLI:** `case --case-id <id> --report <path.json>` and `suite [--case-id <id>…] --report <path.json>` against the committed catalog only (no URLs, live mode, or workflow user catalog/pin overrides). Local unit use may call `evaluate_bundle` directly with bytes + trusted SHA.
**Exact types (implementation must match):**

```python
Mode = Literal["fixture", "replay"]
Verdict = Literal["PASS", "REGRESSION", "EXECUTION_ERROR"]


@dataclass(frozen=True)
class ExpectedOutcome:
    exit_code: int
    verdict: Verdict
    reason_code: str
    expected_count: int | None
    actual_count: int | None


@dataclass(frozen=True)
class CiCatalogEntry:
    case_id: str
    bundle_path: Path
    expected_sha256: str
    mode: Mode
    fixture_count: int | None
    variant: Literal["none", "flip_first_byte"]
    expectation: ExpectedOutcome | None


@dataclass(frozen=True)
class CaseResult:
    schema_version: Literal[1]
    case_id: str | None
    verdict: Verdict
    exit_code: int
    reason_code: str
    expected_count: int | None
    actual_count: int | None
    bundle_sha256: str | None
    mode: Mode | None
    provenance: str | None
    provider_calls: Literal[0] = 0
    cost_usd: Literal[0] = 0


@dataclass(frozen=True)
class SelfTestCheck:
    expected: ExpectedOutcome
    actual: CaseResult
    matched: bool


@dataclass(frozen=True)
class SuiteReport:
    schema_version: Literal[1]
    kind: Literal["PIPELINE_SELF_TEST"]
    selected_ids: tuple[str, ...]
    checks: tuple[SelfTestCheck, ...]
    verdict: Verdict
    exit_code: int
    reason_code: str
    provider_calls: Literal[0] = 0
    cost_usd: Literal[0] = 0


def evaluate_bundle(
    zip_bytes: bytes,
    *,
    trusted_sha256: str,
    mode: Mode,
    fixture_count: int | None = None,
    case_id: str | None = None,
) -> CaseResult: ...
def parse_catalog(data: bytes, *, catalog_dir: Path) -> tuple[CiCatalogEntry, ...]: ...
def matches_control(actual: CaseResult, expected: ExpectedOutcome) -> bool: ...
def run_catalog(case_ids: Sequence[str] | None = None) -> SuiteReport: ...
def main(argv: list[str] | None = None) -> int: ...
```

`case_id` is 1–64 ASCII letters/digits/underscore/hyphen; catalog paths are relative beneath the fixed catalog directory, with no symlink components. The first increment supports the repository's locked **editable** install only: catalog lookup is anchored to the trusted module's repository root (`Path(__file__).resolve().parents[3] / "tests/fixtures/regression_ci/catalog.json"`), never the current directory or environment. Missing checkout/catalog fails closed; wheel distribution is outside scope. Fixture counts are strict integers 0–1000 and required only in fixture mode. No CLI count/pin/path overrides exist; unit/integration callers can use the Python API explicitly.

`parse_catalog` is the explicit validation seam for catalog JSON bytes and a caller-supplied test/integration directory; it returns validated entries without executing any case. `run_catalog` reads only the fixed trusted catalog path and calls this parser with that path's parent. Tests feed temporary catalog bytes/directories through `parse_catalog`; neither workflow nor public CLI accepts a replacement path or pin.

All verification and extraction finish on the same immutable bytes before invocation. The parent re-hashes every extracted file against the corresponding already-verified in-memory ZIP member and requires the exact same member set. This is independent of the child runner's own subsequent `standalone.verify`. The owned snapshot is outside the checkout with no symlink components; parent sets files to `0400` and directories to `0500` before invocation, then restores owned directory/file modes to `0700`/`0600` in `finally` before cleanup. Invoke `sys.executable -I -m counterforge_regression.ci` with an internal child mode (not a live mode), the fixed snapshot path and validated mode/count via an argv list. This internal child entry is distinct from the public `case`/`suite` CLI; it accepts fixture/replay only, never live mode or an alternate catalog/pin. Child environment is exactly `PATH` plus a fresh temporary `HOME`; no inherited model keys, tokens, secrets, live flags or `PYTHONPATH`. The shim is part of the trusted module and imports only trusted installed `standalone.run`.

Suppress runner prints with a capped sink; emit exactly one protocol JSON of at most **8 KiB**. A count mismatch is identified by the caught `AssertionError` from vetted `run`, never by process exit alone. Extract integer expected/actual values only from that trusted runner's anchored `expected=... actual=...;` assertion prefix; a different assertion is an execution error. `standalone.ExecutionError` maps to `RUNNER_EXECUTION_ERROR`; any other exception maps to `CHILD_EXECUTION_ERROR`. Parent enforces **30 seconds** and at most **8 KiB each** of stdout/stderr, then validates protocol fields, enum/ranges and exit agreement. Oversize, absent or malformed protocol maps to `UNKNOWN_CHILD_OUTPUT`; timeout maps to `CHILD_TIMEOUT`. No captured text is echoed. The parent reports no interpretable count on an execution error.

Report paths must end in `.json`, have an existing parent directory and no symlink component, and cannot refer to a bundle/catalog/input file. Atomic writes use a sibling temporary file and replace a regular prior report only; JSON is at most **16 KiB**. On an unsafe/unwritable destination, exit 2 and emit a bounded safe `REPORT_PATH_UNSAFE` or `REPORT_WRITE_ERROR` record to stdout; do not fall back to another destination.

**Baseline pin (root-measured at `e0efa1e`, locked venv):** existing `scripts.verify_export.fixture_bundle()` is 8,234 bytes, SHA-256 `e91352ad992ea233cd6429b18218b92de796b2551ff4193896713428bc46b6a6`. It is a tiny harness constant-output fixture, not visually verified real five objects. Implementation must regenerate and compare bytes to this pin before committing the retained archive/catalog; a different hash is never auto-accepted. The retained bytes are the source of truth; regeneration is a reproducibility check. The coordinator retains the exact measured archive for this first implementation. If a later encoder/zlib/locked-dependency change makes regeneration differ, first verify and use the retained bytes against their original outer pin and the unchanged trusted v1 templates. If retention is missing or an intentional re-pin is needed, stop and propose a separate reviewed pin diff recording the old/new hashes, dependency/encoding reason and compatibility results. A maintainer must review that exact diff; never silently regenerate the catalog to green.

## 4. Catalog and real-bundle use
The retained archive is `tests/fixtures/regression_ci/v1-fixture.zip` and the catalog is `tests/fixtures/regression_ci/catalog.json`. One deterministic committed v1 `LOCAL_FIXTURE` ZIP plus an owner-reviewed catalog live under `tests/fixtures/regression_ci/` (created only at Plan 12 implementation, not now): `{"schema_version": 1, "cases": [CiCatalogEntry, …]}`, **max 10 cases**, unique `case_id`; several cases may reuse the same path/SHA with different modes/counts, but conflicting SHA pins for one path are rejected, each SHA measured from locked deps/trusted builder and pinned by review; pin updates are reviewed diffs, never auto-trust of self-reported hashes. The workflow uses this fixed catalog only. Suite expectations (`ExpectedOutcome` exit+verdict+reason+counts) are allowed **only** for these shipped fixed synthetic self-test cases, never for arbitrary real exports; a genuine per-case consumer exit 1 always fails. For actual owner-reviewed bundles, a future reviewed catalog addition (local path + SHA) is the template — nothing is fetched remotely.

The fixed `tampered` control alone may set `variant="flip_first_byte"`: flip byte zero of an owned in-memory archive copy and retain the original trusted SHA. All other cases use `variant="none"`. Never mutate the retained archive. Only the four shipped synthetic IDs may carry expectations; future owner-reviewed real entries have none and are allowed only with `case`, not the self-test `suite`. Empty, duplicate-ID, conflicting-pin or >10-entry catalogs fail closed. Unknown selected IDs and real entries selected for `suite` fail closed; ordinary reviewed entries may have their own safe IDs but no expectations or mutation variant. Expectations/variants on any other ID are rejected. Multiple ordinary entries sharing identical path/SHA are valid.

## 5. Report schema v1 and reason codes
Exit `0/1/2` ⇔ `PASS/REGRESSION/EXECUTION_ERROR`. Safe reason codes: `OK`, `COUNT_MISMATCH` (regression only), `BUNDLE_SHA_MISMATCH`, `BUNDLE_INVALID`, `CATALOG_INVALID`, `INVALID_INPUT`, `REPORT_PATH_UNSAFE`, `CHILD_TIMEOUT`, `CHILD_EXECUTION_ERROR`, `UNKNOWN_CHILD_OUTPUT`, `RUNNER_EXECUTION_ERROR`, `REPORT_WRITE_ERROR`, `SELF_TEST_MISMATCH`. Reports are JSON objects written to the report path and echoed bounded to stdout; workflow summaries render values via a small Python reader over the JSON (no direct `${{ … }}` interpolation into shell, no artifact-Markdown injection from bundle-controlled strings — provenance/mode come from a fixed allowlist or are omitted if unexpected).

## 6. First shipped workflow: `PIPELINE_SELF_TEST`
`.github/workflows/ci-regression.yml` (new, separate consumer job; existing CI already tests PR-head source) first ships **only** the fixed catalog suite over the committed retained fixture (§3 baseline pin; tiny synthetic harness image, **not** real visual ground-truth discovery). Trust roots: for `pull_request`, checkout/install the trusted **base SHA** (`pull_request.base.sha`, base `master` only) and take catalog+archive from that same base — never PR head; for `push`/`workflow_dispatch` on `master`, use the owner-reviewed/`github.sha` code+ catalog. Non-`master` manual refs are denied. There are no workflow dispatch inputs. The job always runs the complete four-control suite; no workflow inputs override catalog or pins. The first `master` addition bootstraps repository CI; do not claim the old `e0efa` base already contains `ci.py`. Four fixed reviewed controls run through the real module:
| Control | Input | Exit | Verdict / reason | Counts (exp/act) |
|---|---|---|---|---|
| healthy | fixture count 5 | 0 | PASS / `OK` | 5 / 5 |
| broken | fixture count 4 | 1 | REGRESSION / `COUNT_MISMATCH` | 5 / 4 |
| replay | recorded responses | 1 | REGRESSION / `COUNT_MISMATCH` | 5 / 4 |
| tampered | fixed `flip_first_byte` variant; original outer pin | 2 | EXECUTION_ERROR / `BUNDLE_SHA_MISMATCH` | — / — |
`matches_control` requires exact equality of exit, verdict, reason and both counts (including `None`). `run_catalog(None)` selects `healthy`, `broken`, `replay`, `tampered` in that order; explicit selections must be nonempty, unique subsets of those four IDs, and reports name the actual selected IDs. A suite stores each expectation, actual result and comparison separately. Suite-level catalog/report/protocol errors use a safe `reason_code` even when `checks` is empty. Successful suites use `OK`. Suite exit 0 means all fixed controls matched; an unexpected execution error has exit 2, and any other expectation mismatch has exit 1 (`SELF_TEST_MISMATCH`). The expected tamper error is the sole permitted expected execution-error control. CI passes **only** when exit, verdict, reason code, and counts all match the fixed control — exit 2 alone never passes the tamper control: child timeout, missing module, or report-write failure produce different reason codes and fail the suite. This expectation inversion exists only for these fixed named `PIPELINE_SELF_TEST` catalog controls; a genuine arbitrary consumer case never accepts a caller expected-outcome override, and its exit 1 always fails the job. Reports/summaries show both actual verdict and self-test expectation, rendered only from structured JSON by a Python reader (no raw child output/artifacts into GitHub Markdown, no `${{ … }}` shell interpolation).

The four workflow controls cover successful consumption, count mismatches and rejection **before** invocation. They do not independently prove the child's infrastructure-error path. That property is proved by Task 2's unit/subprocess tests, executed on PR-head code by the existing `ci.yml` job. The base-SHA consumer job does not validate a PR's proposed wrapper/catalog/archive changes; a green base run is only a check of the retained trusted consumer. The existing PR-head unit job and code review are therefore required, followed by an actual post-merge master consumer receipt. Canceled, skipped or missing runs have no passing receipt; record them as pending and inspect an actual completed run.

## 7. Compatibility
Existing gates stay authoritative: `make verify-export` ([`scripts/verify_export.py`](../../scripts/verify_export.py)), `tests/integrations/test_export_runner.py`, and `tests/unit/test_regression_bundle.py` including the old-format readable bundle test. The new behavior needs unit tests, subprocess checks and the existing export/integration compatibility checks. It changes no app/schema, so a full DB/browser suite is unnecessary. Remote workflow verification remains pending until an authorized push; no deliberate failing commit is pushed to master merely to test a negative control.

## 8. Live follow-up (explicitly outside the first increment)
**Not FR-20 live-complete.** No live workflow, credential setup, or active allowance exists now (allowance: 0 calls / $0). A future, separately approved design must require: default-branch manual dispatch only; a protected GitHub environment with required reviewers holding a dedicated credential; durable operator accounting that renders portable `unknown` cost (never an invented dollar ledger) with its own durable job deadline/accounting design (the existing 45 s is a per-request timeout, not an overall wall-clock bound); ceiling of 1 call, ≤4096 tokens, concurrency 1; a freshly authorized funded envelope with current pricing checked at dispatch; unknown-charged operations never retried. Existing Phase 5–7 live envelopes are not reused.

## 9. Non-goals
No new provider, app/UI, schema, bundle-format, schedule, remote fetch, or live inference changes. No modification of Phase 10 snapshot metadata or its pending public release gates.
