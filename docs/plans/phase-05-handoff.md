# Phase 05 implementation handoff

Phase 04 commit `9bf51c9` was pushed to `origin/master` before this work. Phase 05
implements the bounded experiment scheduler and initial owner cockpit directly on
`master`, following the MVP workflow in `AGENTS.md`.

## Delivered behavior

A committed experiment intent freezes its VTC, exact source/mask assets, target
prompt/settings, provider mode and adapter/verifier/pricing revisions. Three
fresh correct original-seed observations are required before planning. An
interpretable incorrect baseline stops search; unknown or provider-error replies
pause for review. Fixture intent cannot turn into paid intent after a deployment
mode change; missing legacy mode snapshots fail closed.

Short ID-only RQ deliveries advance persisted checkpoints under a runner fence.
P0 ADAPTIVE uses one real Spark proposal (`spark-initial-v1`), with an explicitly
marked deterministic fallback on invalid/unavailable output. RANDOM_BASELINE is
seeded and makes no planner request. This phase processes one bounded proposal;
feedback planning and continued exploration belong to Phase 07. Candidate
creation, normalization and independent validation reuse Phase 04. Only the
latest sealed VALID evidence permits target screening. Uncertain semantics and
visibility require audited human review, with a visible paused state.

One incorrect screen creates SUSPECTED. Three fresh canonical confirmation slots
use identical retained candidate bytes, prompt and settings; screening is excluded.
Three incorrect interpretable confirmations establish STABLE; mixed outcomes are
INTERMITTENT, three correct are NOT_REPRODUCED, and incomplete/unknown outcomes
remain REVIEW_REQUIRED. Demo approval is a separate audited owner action. Later
invalidity removes confirmed/demo eligibility.

Planner, image, SAM, screening and confirmations use the existing reservation and
usage ledger. Confirmation batches reserve every remaining slot atomically.
Controls drain in-flight work and stop the next dispatch. Runtime includes pauses.
Expired delivery recovery safely retries unsent work, retains ambiguous charges,
and reuses finished target receipts without another call. Receipt replay checks
the reservation's invocation fingerprint, shares the fresh adapter reply decoder,
and binds observations to the exact retained usage/raw-response row. Missing
non-target results require operator resolution instead of paid replay.

The browser starts runs with explicit limits and same-mount idempotent retries,
shows candidate reasons, findings/evidence links, baseline progress, controls and
persisted events. It displays committed exposure (`spent + reserved`) and remaining
headroom; actual usage, estimates and unresolved amounts are labeled as included
components. Tightened limits survive contract changes. Private SSE uses owner
cookies, server-only upstream credentials and Last-Event-ID; reconnect refreshes
the authoritative snapshot, and terminal runs close the stream and load history.
An unresolved provider result disables Resume even when its monetary amount is zero.
A page reload may create a new start key; inspect Previous runs before submitting
a fresh start after an uncertain reply across navigation.

Muse Image returns a supported aspect ratio rather than exact source dimensions.
Only generated backgrounds are center-cropped/resized with the recorded
`background-cover-center-bicubic-v1:Pillow-13.0.0` policy. Original provider bytes,
original geometry and normalized bytes remain retained. Source, foreground and
instance masks are never resized by that policy.

## Running and recovery

Set `ENQUEUE_JOBS=1` in both API and worker environments. PostgreSQL is the durable
intent/evidence authority; Redis carries canonical UUID-only notifications. Worker
startup recovers queued/expired setup and experiment work. `make recover-jobs`
performs reconciliation/requeue without starting another worker. Health/readiness
checks never dispatch providers. Shared queue limits cover setup and experiment
intents; provider concurrency remains one.

## Verification

The coordinator independently verified 307 unit/contract tests, 44 scheduler
integration cases, all five real-process browser tests, Ruff formatting/lint,
Mypy (80 source files), generated OpenAPI/TypeScript drift, web lint/typecheck/
production build and Compose configuration. The final full PostgreSQL suite passed all 183 cases in 192.45 seconds, including
44 bounded scheduler cases and the planner-provenance scope/mismatch checks.
The final five browser cases passed in 33.28 seconds. All 495 tests passed across
these separate socket-blocked unit/contracts and loopback-only DB/browser runs;
live tests were separately bounded and are not included in that count.

The production API image builds with non-editable installed packages; network-
disabled container imports cover API, experiment dispatch and recovery. The
standalone web image builds successfully. Browser checks use real PostgreSQL,
Redis/RQ, API and Next processes with fixture SDK transport. They cover start
retry after a lost successful response, review/resume, private SSE cursor replay,
terminal stream closure, pause/cancel, stored baseline rejection and unresolved
result display, real fixture budget exhaustion, desktop/mobile layout and the
inherited source/mask/candidate review flows. Startup worker registration is
awaited before fixture provisioning to avoid recovery racing manual intents.

Receipt and mode-switch regressions reproduced failures before fixes. The live
run exposed the actual stored Meta response envelope, uncertain SAM checkpoint
handling and cancellation after supporting evidence; three more offline cases
reproduced those defects before repair. Hidden reasoning exhausted the original
1024-token planner cap. Planner template `planner-v2-low-4096` explicitly selects
low reasoning and 4096 output tokens, retaining full conservative reservation.
Official [reasoning](https://dev.meta.ai/docs/reasoning) and
[pricing](https://dev.meta.ai/docs/pricing-rate-limits) docs were checked 2026-10-29.

No tests fabricate real human approval: browser/integration decisions are labeled
simulated. The live source and candidate decisions below record explicit user
replies. Disposable checks only use owned schemas, queues and containers. The
live graph and PostgreSQL backup are saved outside test cleanup under ignored
`data/phase05-live/`, with immutable provider and image artifacts.

## Live evidence and remaining gates

The user approved a cumulative **USD 15 / 20 API-call** live-test envelope and the
shown CC0 `fixtures/phase03/five-blue-containers.png` source, human count 5 and five
exact rectangular masks on 2026-10-29. Source approval is recorded through normal
seed/mask services and audit events, on behalf of the explicit chat approval.
The first test restricted the approved mutation scope to background replacement,
one candidate, seven target calls and at most nine non-target attempts. After its
six known settled calls, the second run tightened its target cap to four, keeping
the cumulative worst case at 19 calls and below the USD 15 envelope.
Generated candidates require separate human semantics/visibility review; the
second candidate received that explicit approval before screening.
Application live usage is separate from development-agent harness usage.

## Team and review

The installed [develop-with-agent-team skill](/home/otto/.codex/skills/develop-with-agent-team/SKILL.md)
and bridge 1.1.0 were used. Editing was serialized in the current checkout, with
terminal status and stopped supervisor confirmed before ownership transferred.
The coordinator integrated the cockpit and independently ran final checks.

| Harness / actual model | Meaningful work |
| --- | --- |
| Codex / `gpt-6.1-sol` | Backend Tasks 1–4 implementation and targeted checks (`b874cb33-4df1-46ac-a58d-995ed1a561e8`). |
| Claude Code / `claude-opus-5` | Independent preflight, receipt/billing recovery review (`6f88883c-82d6-4674-af10-72db082d0453`) and fix verification (`13b8fe3e-15be-4d5d-838b-71889f651f99`); seven receipt findings resolved, no new blocker in the reviewed surface. A broader review timed out, and a later review hit Claude's session quota;
  no completed verdict is claimed for either attempt. Muse reviewed the final follow-up fixes. |
| Muse / `muse-wonder-1.0` | Acceptance preflight and UI/auth review (`e495555a-6709-4708-8fa2-4c9469834691`), identifying exposure clarity, tightened-limit preservation, terminal SSE, unresolved-result and evidence-link gaps. Final backend follow-up review (`f0646fe4-fc46-42a4-8c44-e4da57a4c0f5`) passed all four repaired surfaces with no blocking defect. Coordinator resolved material findings and expanded browser coverage. |

## Phase 06 boundary

Export only retained genuine evidence with current VALID ground truth. Synthetic
fixtures, partial confirmations and pending human reviews cannot establish a real
model weakness. No finding is promised from a bounded search. Phase 06 owns the
portable regression bundle and healthy/broken target runner; Phase 07 owns
feedback search, refinement and version retests. Automatic semantic/visibility
threshold calibration and held-out evaluation remain later gates.

### Bounded live runs (2026-10-29)

- First experiment: `7ec052b1-6db5-4db1-8ce1-e37bbb806ba2`. Three correct baseline
  replies; planner INCOMPLETE at 1024 tokens and explicitly labeled fallback;
  Muse Image completed; SAM returned zero instances. Cancelled after retaining
  all evidence, with no screening. Six dispatched calls, USD 0.020320 usage-based
  actual cost. No old paid operation was replayed.
- Second experiment: `9bff00b1-81a8-43bf-82cc-42349cc6d791`. Three correct baseline
  replies; actual META_SPARK structured plan using the revised request; real Muse
  Image background retained and mapped to source geometry; SAM again returned
  zero instances. A tighter four-target-call / USD14.9 run limit bounds cumulative
  attempts at 19 and cumulative conservative dollars below15, including the first run.
- Candidate `dd1c33f4-228c-4391-9ad0-33925dc267cf`, image SHA256
  `992ef9aedd6b7a86bc7731975de55de105bb26708d7d265067ffac2e84d436cc`:
  Otto explicitly approved count 5, intact semantics and clear visibility in Codex
  after being shown source/candidate and informed SAM detected 0. Review binds
  prior report `b5252b4c3f1b40234de153d50bd3fbc3a458bf1d6b2d4d5a48891c1349f2e789`.
- After the explicit candidate review, screening returned **CORRECT, count 5**.
  The second experiment reached **COMPLETED / BOUNDED_PLAN_COMPLETE** with four
  target calls. No finding or confirmation was created; no real model weakness
  is claimed. The run cap would have prevented a fresh three-call confirmation
  batch if screening had been incorrect.

| Application category | Dispatched calls | Usage-based actual USD |
| --- | ---: | ---: |
| Target (six seed checks, one screen) | 7 | 0.007801 |
| Spark planner | 2 | 0.008711 |
| Muse Image | 2 | 0.020000 |
| SAM | 2 | 0.005000 |
| **Total** | **13** | **0.041512** |

All 13 ledger entries are SETTLED/ACTUAL. Estimates, unresolved charges and
reservations are zero. Dollars are calculated from retained token/image usage and
published standard-mode prices, rounded conservatively per ledger entry; an API
credit balance or invoice was not queried. Agent-team harness usage is excluded.
No further paid calls were made.

The local evidence root is `data/phase05-live/` (ignored, preserved): `graph.json`,
`summary.json`, both experiment IDs/settings, exact human approval,
`evidence.pgcustom`, 37 verified immutable assets and `manifest.json` SHA256s.
The database schema is `cf_phase05_live_980fe9ea4b57484e87caab01972822e7`.
The backup requires the migration prerequisites, including pgcrypto in public;
no portable regression export is claimed before Phase 06. The backup was restored
into a separate owned temporary database and verified to contain all 13 usage rows
and USD 0.041512. That restore-check database was then removed. Task-owned database
and Redis containers are removed after verification; the backup, graph and all
content-addressed bytes remain available locally for Phase 06.

The initial scheduler generated an extra candidate plan labeled OWNER_APPROVED.
That label was execution provenance, not an actual human plan-review event. The
historical immutable candidate records remain unchanged, with append-only audit
annotations linking their exact normalized plans to the retained original
META_SPARK/fallback records. Both annotations were checked against canonical
`approve_plan` output. New scheduler candidates reference the actual retained
planner proposal directly; foreign or changed proposals fail closed. The planner
settings are retained through the evidence sanitization allowlist. A regression
reproduced the misleading label before this fix.

Actual Meta access is demonstrated for Spark, Muse Image and SAM. SAM supplied no
usable instance proposals for this illustration; that perception-quality gate
remains explicit. Human-approved masks/count remain authoritative. There is no
claim of a real model weakness or stable finding from baseline success or provider
access. Phase 04 calibration specimens still have only simulated acceptance.
