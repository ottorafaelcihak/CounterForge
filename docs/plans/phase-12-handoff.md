# Phase 12 — Offline regression CI handoff

**Status:** Implemented and verified locally on `master`, 2026-11-01, after the
owner approved [Plan 12](plan-12.md) and [the CI design](phase-11-ci-design.md).
The GitHub workflow execution receipt remains pending an authorized push.
Live CI remains a separate future design and funding decision.

## Delivered behavior

The trusted `counterforge_regression.ci` module consumes a fixed, source-anchored
local catalog. It checks the reviewed outer SHA-256 and existing bundle verifier
before extracting an owned snapshot outside the checkout. Extracted files are
rehash-checked and read-only during execution. The isolated Python child invokes
the installed `standalone.run` with only `PATH` and a fresh `HOME`; bundle code,
tests and requirements are never executed or installed. Snapshot permissions are
restored for cleanup on success and failure.

A case returns structured JSON with exit `0` for `PASS`, `1` for a count
`REGRESSION`, and `2` for `EXECUTION_ERROR`. The parent validates the child JSON
and exit together, captures at most 8 KiB per output stream while reading, and
limits each child to 30 seconds. Reports use safe atomic `.json` writes and a
16 KiB limit, with fixed reason codes and zero provider calls/cost. Unknown
answers, malformed protocols and infrastructure failures cannot satisfy the
count regression contract.

The retained synthetic harness archive is 8,234 bytes, regenerated and compared
byte-for-byte before inclusion:

`e91352ad992ea233cd6429b18218b92de796b2551ff4193896713428bc46b6a6`

It contains the existing tiny synthetic runner fixture. Its count of five is a
harness setting; it supplies no human-reviewed visual ground truth or new real
model finding. The four committed controls share the same archive and pin:

| Case | Exit | Verdict | Reason | Expected / actual |
| --- | --- | --- | --- | --- |
| `healthy` | 0 | PASS | OK | 5 / 5 |
| `broken` | 1 | REGRESSION | COUNT_MISMATCH | 5 / 4 |
| `replay` | 1 | REGRESSION | COUNT_MISMATCH | 5 / 4 |
| `tampered` | 2 | EXECUTION_ERROR | BUNDLE_SHA_MISMATCH | null / null |

The tamper case flips one byte in an owned memory copy. The retained file stays
unchanged. Only the fixed `PIPELINE_SELF_TEST` suite matches these expected
negative outcomes; a direct broken `case` continues to fail with exit 1.
Ordinary future reviewed catalog entries cannot define expected-exit masking.

## Local use

From the repository after the existing locked environment setup:

```sh
.venv/bin/python -I -m counterforge_regression.ci suite --report /tmp/counterforge-suite.json
.venv/bin/python -I -m counterforge_regression.ci case --case-id healthy --report /tmp/counterforge-healthy.json
```

The report parent must already exist; symlink components and overwriting catalog
or bundle inputs are rejected. The CLI accepts only committed case IDs, with no
remote URL, catalog override, pin override or live mode. Local suite selection
may specify a unique subset of the four synthetic IDs. The workflow always runs
all four. A genuine future consumer uses `case` after a separate owner-reviewed
catalog addition with its local archive path and outer SHA.

## Workflow boundary and dependencies

`.github/workflows/ci-regression.yml` runs on `master` pushes, PRs targeting
`master`, and manual dispatch on `master`. PRs use the trusted base SHA for code,
catalog and archive. The existing `ci.yml` unit job covers PR-head source;
base-SHA execution does not validate a PR's proposed consumer changes. The first
master push bootstraps the new module and catalog.

The job uses `contents: read`, a ten-minute timeout and PR-only concurrency
cancellation. There are no dispatch inputs or model-secret references. A static
reader validates the complete bounded JSON before rendering the summary. An
always-run artifact step retains exactly `suite.json` for 14 days. Master runs
are allowed to finish even when a later master event arrives.

Action versions and full commit pins were verified against official release tag
references and pinned `action.yml` files on 2026-11-01:

| Action | Version | Commit | Official reference |
| --- | --- | --- | --- |
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` | [Tag reference](https://api.github.com/repos/actions/checkout/git/ref/tags/v7.0.1) |
| astral-sh/setup-uv | v10.3.3 | `68559e99cc807c1eb7e9238fd43a15bba460eec2` | [Tag reference](https://api.github.com/repos/astral-sh/setup-uv/git/ref/tags/v10.3.3) |
| actions/upload-artifact | v7.0.2 | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` | [Tag reference](https://api.github.com/repos/actions/upload-artifact/git/ref/tags/v7.0.2) |

All three actions declare Node 24. Setup uses Python 3.12 and uv 0.13.6, followed
by `uv sync --locked`. No project dependency or lockfile changes were required.

## Verification

Coordinator checks on the completed implementation:

| Check | Result |
| --- | --- |
| `make lint` | PASS: Python lint/format and web lint |
| `make typecheck` | PASS: mypy, 118 source files; web TypeScript |
| `make test-unit` | 395 passed, including 129 new CI-consumer checks |
| Export integration + bundle unit compatibility | 19 passed |
| `make verify-export` | PASS: healthy=0, broken=1, replay=1, tamper=1, optimized=1 under the existing v1 runner |
| `make release-check` | 0 structural errors; `release_ready=False` |
| Isolated CLI outside checkout with injected dummy model key/live flags | All four case exits above and suite exit 0; report calls/cost 0 |
| Fixture regeneration | Byte-equal; retained size and SHA unchanged |
| actionlint 1.7.12 | PASS on the new workflow |
| `git diff --check` | PASS |
| Gitleaks 8.30.1 on the eleven intended changed files, including the ZIP contents | 0 findings |

The v1 standalone tamper exit in `verify-export` remains 1 by its existing
contract; the new CI consumer classifies the outer-pin failure as execution
error 2. The new tests also exercise hostile ZIPs, altered runner templates,
strict catalog/protocol types and duplicate keys, timeout/output limits, child
environment isolation, snapshot cleanup, unsafe report destinations and hostile
summary JSON. Existing bundle/manifest/standalone implementations remain intact.

The first full lint attempt identified an existing unformatted Python example
in the approved design. Ruff formatted that example without changing its API or
design semantics, then the full gate passed. The actionlint binary came from the
[official v1.7.12 release](https://github.com/rhysd/actionlint/releases/tag/v1.7.12),
with its archive SHA verified against the official checksums:
`8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`.
Shellcheck and pyflakes integrations were disabled; actionlint's own workflow
validation passed. No YAML parser dependency was added.

Database, Redis, full E2E and browser checks were outside this increment: no
application, schema, UI or service code changed. The named export integration
checks are subprocess-only and require no database. All tests used offline
fixtures. **New Meta API usage: 0 calls, USD 0 actual, USD 0 unresolved.**

## Review and remaining receipts

The required three-provider workflow used Codex (`gpt-6.1-sol`) for the bounded
implementation and tests, Muse (`muse-wonder-1.0`) for independent acceptance and
boundary analysis, and Claude Code (`claude-opus-5`) for completed-code review.
The coordinator integrated documentation and independently ran the checks above.
Claude found no false-green, secret, live or bundle-code execution path. Its
ignore-rule concern was checked against Git: the fixture was already tracked,
so subsequent modifications would be retained despite the general `*.zip` rule.
A narrow exception now explicitly documents the one approved fixture path, and
`git check-ignore --no-index` confirms it is excluded from the ignore policy.
All other ZIP exports remain ignored. Four additional tests retain Claude's
successful probes for consistent regression/execution-error summaries and a
lying PASS report. Muse reviewed these final follow-ups separately.

Snapshot preparation, rehash or cleanup failures currently share the fixed
`CHILD_EXECUTION_ERROR` reason with child setup failures, even when invocation
has not started. They still return execution error 2 with null counts. This
minor triage limitation does not change the pass/fail boundary.

- GitHub workflow run URL and four-control artifact: **pending authorized push**.
  Local syntax and protocol checks do not establish a successful hosted run.
- FR-20 live execution: deferred; no new paid authorization or credential setup.
- Workstreams B–D remain untouched roadmap items.
- Phase 08's measured benchmark, genuine stable-failure discovery, and Phase 10's
  public access/video/submission gates remain outstanding. Frozen Phase 10 release
  metadata and its `release_ready=False` status are unchanged.
