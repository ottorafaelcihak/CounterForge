# Submission and acceptance matrix (draft, not submitted)

Status: **draft only**. `release_ready: false`. No submission or
publication is claimed. Repository (root-verified 2026-10-30: existing repo is PUBLIC,
default `master`): https://github.com/ottorafaelcihak/CounterForge
Original code license: **MIT** (owner-confirmed 2026-10-30; see
[LICENSE](../LICENSE)). Unauthenticated access: root-verified
HTTP 200, frozen README bytes match the local Git blob.
Video URL and public product URL: **null** (none invented).
No publication or submission receipt exists; the retained demo
snapshot is a private DRAFT with 0 findings, unpublished.

Event pages checked **2026-10-30**:
[Global Hackathon](https://dev.meta.ai/events/global-hackathon) and
[participant details](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details).
The public event page confirms **November 4, 21:00 ET =
November 5, 03:00 Europe/Madrid**. Current rules, form fields, track,
eligibility, team-size and judging-weight details on the gated
participant pages were **inaccessible via web HTML** at check time,
so they are **pending verification** — no weight, team-max, or track
claim from the source documents is asserted as current fact.

## How to read statuses (scoped, never a global PASS)

- `fixture_verified` — proven only on synthetic/local fixtures.
- `live_verified` — real-provider evidence for this scoped claim.
- `partial_live` — some real-provider evidence; claim incomplete.
- `unmet` — the stated goal was attempted/searched and not achieved.
- `pending` — not run, or a human/root gate is outstanding.
- `pending_root` — coordinator verification is prepared but has not run.

A `fixture_verified` row never implies a real model weakness, a
live result, or release readiness. Run revision `8029ff2` is the frozen runtime for fresh **local**
quality verification (clean archive SHA
`91a40698abce0fcfe9453259a70c42bf1065af96088b196deddf4391a9c9845a`:
lint/mypy/TS/schema, unit 245, contracts 173, export, web build,
Node 13, E2E 15, ops 2 — all PASS locally; fresh DB suite 280 PASS (2 expected ops SKIPs); p95 on a small fixture, 20 samples:
live 0.60ms, ready 9.14ms, projects 1.57ms; public latency
unmeasured). Historical real-provider reports retain their source
commits/evidence refs separately; their live-call revision is
unknown (null), **not** an 8029 live call. The new Pause/test/tooling
delta is validated against the current Phase 10 workspace, not the
frozen 8029 source; the active-call Pause regression passed (1 test, 2.35s).
No new live execution is claimed. A private 180-second silent captioned video draft is verified; its hash and decoded-frame receipt are in [demo verification](releases/demo-verification.json). Product/video publication and public access remain pending.

## PRD acceptance (12)

| ID | Status | Evidence / test name | Run revision / date | Deviation |
| --- | --- | --- | --- | --- |
| PRD-AT-01 | partial_live | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/e2e/test_seed_setup.py` | 8029ff2 / 2026-10-29 | Development CC0 seed only; not a held-out dataset case |
| PRD-AT-02 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/e2e/test_experiment_cockpit.py` | 8029ff2 / 2026-10-30 | No live seed was baseline-wrong; rejection proven on fixtures |
| PRD-AT-03 | fixture_verified | [Phase 04 handoff](plans/phase-04-handoff.md); `tests/unit/test_invariants.py` | 8029ff2 / 2026-10-29 | Fixture specimens, simulated acceptance only |
| PRD-AT-04 | fixture_verified | [Phase 04 handoff](plans/phase-04-handoff.md); `tests/unit/test_invariants.py` | 8029ff2 / 2026-10-29 | No live ambiguous-occlusion case claimed |
| PRD-AT-05 | partial_live | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/unit/test_compositor.py` | 8029ff2 / 2026-10-29 | Live candidate screened CORRECT (count 5); no live failure followed |
| PRD-AT-06 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/unit/test_reproduction.py` | 8029ff2 / 2026-10-30 | Three-fresh rule proven on fixtures; live fresh confirmations: 0 |
| PRD-AT-07 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/unit/test_reproduction.py` | 8029ff2 / 2026-10-30 | Intermittent classification fixture-only |
| PRD-AT-08 | fixture_verified | [Phase 06 handoff](plans/phase-06-handoff.md); `make verify-export` record | 8029ff2 / 2026-10-29 | **No live export exists**; fixture bundle only |
| PRD-AT-09 | fixture_verified | [security notes](security.md); `tests/unit/test_regression_bundle.py` | 8029ff2 / 2026-10-30 | Gitleaks 8.30.1 source/history: 1 explicit public dummy rehearsal token at `scripts/rehearse_ops.sh:18`, triaged, 0 untriaged; generated `.next` PRIVATE framework keys excluded from public archives, static client 0 findings; pip-audit/npm audit 0 vulns (measured) |
| PRD-AT-10 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/unit/test_job_queue.py` | 8029ff2 / 2026-10-30 | Restart/cap proof on disposable fixture services |
| PRD-AT-11 | pending | [README](../README.md); [ATTRIBUTION](../ATTRIBUTION.md) | phase10_delta (runtime8029) / 2026-10-30 | MIT LICENSE added (owner-confirmed); fresh-export local gates measured by root, frozen clean DB suite 280 PASS (2 expected ops SKIPs); artifact/test belongs to Phase10 delta, not frozen8029 |
| PRD-AT-12 | pending | [demo storyboard](demo/storyboard.md); [Phase 09 handoff](plans/phase-09-handoff.md) | 8029ff2 / 2026-10-30 | Verified180s private draft; product/video publication and public access pending |

## Technical specification acceptance (20)

| ID | Status | Evidence / test name | Run revision / date | Deviation |
| --- | --- | --- | --- | --- |
| TS-AT-01 | partial_live | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/contracts/test_vtc.py` | 8029ff2 / 2026-10-29 | Live use on the one development seed only |
| TS-AT-02 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/contracts/test_api.py` | 8029ff2 / 2026-10-30 | See PRD-AT-02 |
| TS-AT-03 | partial_live | [provider record](plans/phase-03-provider-record.md); `tests/contracts/test_sam.py` | 8029ff2 / 2026-10-29 | Live SAM returned 0 usable instances (INCOMPLETE); human masks authoritative |
| TS-AT-04 | fixture_verified | [Phase 04 handoff](plans/phase-04-handoff.md); `tests/unit/test_compositor.py` | 8029ff2 / 2026-10-29 | Fixture validity evidence only |
| TS-AT-05 | fixture_verified | [Phase 04 handoff](plans/phase-04-handoff.md); `tests/unit/test_invariants.py` | 8029ff2 / 2026-10-29 | No live extra-object finding attempted as a claim |
| TS-AT-06 | partial_live | [Phase 06 handoff](plans/phase-06-handoff.md); `tests/unit/test_provider_lease.py` | 8029ff2 / 2026-10-29 | Real Spark image timeout retained as UNKNOWN up to USD 0.012, not retried |
| TS-AT-07 | fixture_verified | [Phase 06 handoff](plans/phase-06-handoff.md); `tests/unit/test_reproduction.py` | 8029ff2 / 2026-10-30 | **Real stable failure: none. 0 live stable findings** |
| TS-AT-08 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/unit/test_reproduction.py` | 8029ff2 / 2026-10-30 | Fixture-only |
| TS-AT-09 | fixture_verified | [Phase 06 handoff](plans/phase-06-handoff.md); `scripts/verify_export.py` | 8029ff2 / 2026-10-29 | Healthy-target pass is the fixture/replay runner |
| TS-AT-10 | fixture_verified | [Phase 06 handoff](plans/phase-06-handoff.md); `scripts/verify_export.py` | 8029ff2 / 2026-10-29 | Broken-target failure is an expected assertion on fixture/replay, not a live model failure |
| TS-AT-11 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/contracts/test_api.py` | 8029ff2 / 2026-10-30 | Idempotency proven offline/integration |
| TS-AT-12 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/unit/test_comparison_usage.py` | 8029ff2 / 2026-10-30 | Budget exhaustion proven on fixtures |
| TS-AT-13 | fixture_verified | [Phase 05 handoff](plans/phase-05-handoff.md); `tests/integrations/test_experiment_pipeline.py::test_pause_during_confirmation_drains_without_next_dispatch` | phase10_delta (runtime8029) / 2026-10-30 | Exact active-call Pause test added in Phase 10 (adapted from the cancel-drain test); root-run PASS (1 test, 2.35s). Prior coverage paused only between calls; artifact/test belongs to Phase10 delta, not frozen8029 |
| TS-AT-14 | fixture_verified | [Phase 06 handoff](plans/phase-06-handoff.md); `tests/unit/test_regression_bundle.py` | 8029ff2 / 2026-10-29 | ZIP safety and final source/fixtureZIP/media scan verified; only explicit dummy rehearsal token |
| TS-AT-15 | fixture_verified | [Phase 09 handoff](plans/phase-09-handoff.md); `tests/contracts/test_api.py` | 8029ff2 / 2026-10-30 | Unauthenticated outsider check pending publication |
| TS-AT-16 | fixture_verified | [Phase 09 handoff](plans/phase-09-handoff.md); `tests/e2e/test_public_demo.py` | 8029ff2 / 2026-10-30 | Real snapshot is a private DRAFT with 0 findings; nothing public |
| TS-AT-17 | pending | [Phase 08 handoff](plans/phase-08-handoff.md); [comparison results](plans/phase-06-comparison-results.md) | 8029ff2 / 2026-10-30 | Phase 06 arm unfinished (Spark timeout); Phase 08 campaign has no 12 human labels/rights/budget, so no lift claim |
| TS-AT-18 | partial_live | [Phase 07 live results](plans/phase-07-live-results.json); [Phase 05 handoff](plans/phase-05-handoff.md) | 8029ff2 / 2026-10-30 | Real Spark/Image/target calls retained; SAM INCOMPLETE (0); formal `make smoke-meta` not run |
| TS-AT-19 | pending | [demo storyboard](demo/storyboard.md) | phase10_delta (runtime8029) / 2026-10-30 | Verified180s private video; frozen public repository README HTTP200; public product/video access pending; artifact/test belongs to Phase10 delta, not frozen8029 |
| TS-AT-20 | pending | [ATTRIBUTION](../ATTRIBUTION.md); [fixture manifest](../fixtures/phase03/manifest.json) | phase10_delta (runtime8029) / 2026-10-30 | MIT LICENSE added (owner-confirmed); manifest has no separate rights notice, owner review pending; dependency inventory measured (62/467/442), no compatibility approval; artifact/test belongs to Phase10 delta, not frozen8029 |

## Reconciled committed records (no invented aggregates)

- Phase 05 live: 13 calls / USD 0.041512 actual, 0 unresolved.
  Screening was CORRECT; no finding. SAM: 0 usable instances.
- Phase 06 comparison: 10 calls / USD 0.011852 confirmed plus up to
  USD 0.012 unresolved; Spark arm unfinished, random screening
  correct, 0 stable findings. Cumulative Phase 05+06 as recorded in
  that handoff: 23 calls / USD 0.053364 confirmed + ≤0.012 unresolved.
- Phase 07 live: 10 calls / USD 0.014801 actual, 0 unresolved;
  real Spark consumed a VALID/CORRECT prior outcome; 0 stable
  findings, no live export.
- Phase 08: harness implemented; measured campaign **not run** —
  no 12 human labels/rights and no paid authorization.
- Root verified the actual five-object fixture export bundle offline
  outside the checkout: healthy exit 0, broken exit 1 (assertion
  expected 5, actual 4), replay exit 1, no API calls; bundle SHA-256
  `34435504971aa19f9b8c16068e03667922e41d445f9a25f8743ada26c9e2c08c`.
- Phase 09: real-provider demo draft is private, 0 confirmed
  findings, unpublished; backup SHA in its handoff is a private
  coordinator artifact, not a public receipt.

## Prepared entry copy (not submitted)

**Project:** CounterForge — Visual Model Reliability Lab

**Short description:** CounterForge turns reviewed visual test cases into
repeatable checks for object-counting models. It combines bounded Meta model
calls, controlled image changes and independent human review, retaining the
evidence, costs and fresh responses behind each verdict. Portable regression
bundles make the expected count executable outside the application.

**What was built:** A Visual Test Contract locks the expected count and protected
objects. Spark proposes bounded search steps, Muse Image supplies scene changes,
and SAM proposes segmentation evidence for human review. Valid candidates reach
the target; a stable finding requires three fresh incorrect confirmations.
The owner UI supports evidence review, pause/resume, export and independent
retests. A separate read-only gallery exposes only explicitly published assets.

**Demonstrated result and limits:** Recorded development runs exercised Spark
and Muse Image; SAM yielded zero usable instances on the development image, so
human masks remained authoritative. The search found zero stable model failures.
The private demo therefore shows the actual recorded search outcome and an
explicitly labeled fixture regression: a healthy target passes and a deliberately
broken target fails its count assertion. The Phase 08 benchmark has not run;
no comparative lift is claimed.

**Entry fields to resolve:** Proposed indie developer track / Push the Frontier
theme, subject to entrant confirmation and current organizer rules. Git author
`otto` is metadata, not verified entrant identity or team attribution.
Repository: <https://github.com/ottorafaelcihak/CounterForge>.
Product URL and published video URL: absent. Original code: MIT; third-party
licenses and asset provenance remain separately documented. This copy is ready
for review and adaptation to the actual form; no form fields or submission
receipt are asserted as verified.

## Missing release gates (all explicit)

Root secret/dependency checks are measured and triaged (above);
inventory/notices and private180s video verification are complete. Still open: organizer
rules/form/track/eligibility verification (guide HTML loading /
network error; current rules UNVERIFIED), publication and
submission authorization and receipts, and any live Phase 08
evaluation (campaign pending: 12 human labels/rights/budget).
License (MIT), local security/inventory and private video checks passed. Only repository public access passed; product/video access remains pending. See the
[evidence index](releases/evidence-index.json) for the machine-readable
list. External-link and scanner verification is **not** performed by
`make release-check`; it must be run and recorded explicitly by root.

Current-workspace full DB/browser suites were not rerun: their full results
are frozen8029-only. The Phase10 test-only delta has one targeted active-call
Pause integration pass and two targeted browser passes. There is no app/schema
change; this scope does not claim a full current-workspace integration run.
