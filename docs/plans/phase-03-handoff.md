# Phase 03 implementation handoff

**Date:** 2026-10-28. **Branch:** `master`. **Base:** `8121b87` (phase 02,
pushed to `origin/master` before this implementation). Phase 03 code and fixture
gates are complete. **Real-provider gate TS-AT-18 remains unmet.** No live Meta
API call was dispatched and no live access or real-model weakness is claimed.

## Delivered behavior

- Bounded Spark planner/target, Muse Image and SAM adapters, central prices,
  semantic attempt identity and explicit actual/estimated/unbilled/unknown billing.
  Safe retries own fresh reservations; ambiguous dispatched calls retain their
  upper bound. Official SAM decoding runs in a bounded, credential-free subprocess.
- Durable PostgreSQL intent with post-commit UUID-only JSON/RQ notification,
  fenced claims, heartbeats, finite runtime, single provider concurrency and
  conservative recovery. A Redis failure does not erase queued intent.
- Owner setup UI and same-origin authenticated asset/API proxy. Seed upload keeps
  original/normalized provenance; curated target checks are separate from the
  later experiment baseline. Server model keys are absent from the web service.
- Source-sized binary mask paint/erase, continuous strokes, bounded undo,
  rectangle controls, missing-instance addition, extra-instance retirement,
  immutable revision history and exact-current-revision approval. Unchanged
  pixels cannot reset approval. Unsaved drafts survive session renewal and have
  navigation/sign-out protection.
- A four-detection/five-object discrepancy blocks a new contract until all five
  distinct corrected masks are approved and that exact set is acknowledged.
  Expected count never changes automatically. Historical VTC assets and approval
  evidence remain resolvable.
- Non-root API/worker/web images, loopback Compose services and CI with disposable
  PostgreSQL, Redis and Playwright. Live dispatch defaults to disabled.

## Verification

These are coordinator-executed checks against the integrated workspace:

| Check | Result |
| --- | --- |
| `pytest tests/unit tests/contracts -q` with default socket blocking | **222 passed**, 4.92s |
| `pytest tests/integrations -q --allow-hosts=127.0.0.1` with explicit disposable test DB | **110 passed**, 81.71s |
| Real-process Chromium seed setup / audited mask correction | **1 passed**, 10.21s (`make test-e2e`) |
| Ruff check / format check | Passed, 119 Python files |
| Strict mypy | Passed, 67 source files |
| Web ESLint / TypeScript / production build | Passed |
| Generated OpenAPI / JSON schemas / TypeScript drift | Passed |
| Compose config with placeholder values and `/dev/null` env file | Passed |
| API, worker and standalone web container builds | Passed |
| Packaged pricing, original fixture and official SAM parser, networking disabled | Passed; four normalized proposals |
| Closed live smoke gate | Expected exit 2 before settings/database/provider I/O |

The browser case uses real Next, FastAPI, PostgreSQL, Redis/RQ and the official
SDK/parser with an explicit zero-dollar fixture allowance. It verifies login,
CSRF rejection, HttpOnly/SameSite cookie attributes, absence of token persistence,
101-record pagination, upload rights/count, target reply five, SAM proposals four,
manual fifth mask, every approval, no-op retention, fast brush continuity, undo,
cancelled breadcrumb/Back/sign-out, session renewal without losing the raster,
revision-two approval reset, old approved evidence, discrepancy acknowledgement,
VTC expected count five and private-asset denial after sign-out. Desktop and
390-pixel mobile screenshots were inspected; mobile overflow was checked.

Tests create random schemas and a unique Redis queue, own their service processes
and artifact directory, then remove only those resources. They never use the
application database as a fallback or flush shared Redis. Test `pgcrypto` stays in
`public` under serialized setup; the supplied baseline SQL is unchanged. Linux
subprocess isolation is required for untrusted image/SAM decoding.

## Independent review and rulings

| Decision / finding | Resolution and practical cost |
| --- | --- |
| Conservative financial provenance | Missing usage remains estimated, ambiguous dispatch remains unknown. Unknown cannot become settled without explicit operator reconciliation; the late-ticket recovery regression confirms its retained upper bound. Lower actual costs retain the shared conservative allowance; proven unbilled results release dollars once and release calls only when unsent. |
| Mask history across retry/shrink/expand | Retired rows receive distinct revisions and preserve prior approvals; repeated segmentation cannot archive the same revision twice. Late proposals cannot overwrite a human-edited generation. |
| Model identity and safe failure evidence | Missing/wrong Spark model echo is invalid with unknown billing. SAM/image schemas may omit the echo. Failed smoke completion cannot claim success; failures expose a job UUID and fixed diagnostic, preserving sanitized evidence without raw exception text. |
| Cleanup and runtime | Processing is bounded as well as dispatch. Heartbeat cleanup cannot mask a committed result; failed session unlock invalidates the physical connection. Exception cleanup scopes reservations to the current delivery. |
| URL downloads / logs | No verified CDN host exists, so the production allowlist is empty and base64 is requested. Named SDK/HTTP wire logging is disabled and secret/media/signed-URL regressions pass. This sacrifices low-level wire diagnostics for privacy. |
| Editor review findings | No-op revisions, stroke gaps, unsaved navigation, session expiry and silent list truncation were corrected and exercised. Cursor validation is capped at 512 characters. Login throttling remains later deployment-hardening scope; this stack is bound to loopback. |
| Runtime recovery tradeoff | Settled-but-lost output requires a deliberate new job; no automatic paid replay is attempted. This can require manual recovery after a crash. |
| Historical contracts | Existing locked VTC GET and same-key started-run replay preserve history. Every new run still requires the current exact mask set to be approved, as accepted in phase 02. |

Targeted regressions first reproduced missing runtime cancellation, unchanged-mask
approval reset, failed heartbeat propagation and failed-unlock reuse, then passed
after fixes. A review suggestion that a late ticket could downgrade UNKNOWN was
rejected after tracing the existing Budget guards and adding a passing adversarial
port regression. A long backend review timed out before its report; a fresh,
bounded read-only review completed and its material findings were resolved.

## Three-provider participation

One editor owned the checkout at a time; no feature branch or worktree was created.
The coordinator integrated and independently checked the result.

| Harness / actual model | Work and evidence |
| --- | --- |
| Codex / `gpt-6.1-sol` | Preflight, bounded package implementation (`ec437429-b11f-4cc7-aaa6-b6f9b6c2dabb`) and backend implementation (`941acd92-1dad-45e8-8d2c-6d37a3203d4c`). Both reached terminal status with supervisor stopped before handoff. |
| Claude Code / `claude-opus-5[1m]` | Independent package/mask/accounting review (`635c7e09-4d4e-4aad-9c64-823ffc741b56`) and focused backend review (`58ec4200-57ff-432e-bbd3-3c71dc7bb590`). First review executed offline checks; focused review was code-only. |
| Muse / Wonder 1.0 CLI default | Official-parser research, acceptance planning and completed UI/BFF review (`b7b4c2bb-348a-45e7-b06c-860f58b96a1e`). Run metadata records `muse-wonder-1.0` / contributor routing. UI review was read-only; no shell checks were claimed. |

Bridge version 1.1.0 fresh CLI status was authoritative for supervisor liveness.
SDK/thread-backed coordinator checks required sandbox escalation because the
normal execution sandbox prevented thread-to-loop wakeup; provider socket blocking
remained active. This is an execution-environment limitation, not a product workaround.

## Carry-forward gates

[Provider record](phase-03-provider-record.md) contains official documentation,
verification date, installed SDK/parser versions, prices and exact adapter ports.
The original CC0 development seed has a manifest and a pixel-geometry regression.
Its automated approvals are test data, not production human sign-off.

Live entitlement/credit, invocable model IDs, an explicitly authorized finite paid
allowance and real four-capability smoke evidence remain unverified. No model
catalog was implemented. `make smoke-meta` stays closed without its complete
LIVE gate and existing owned/reviewed references. Phase 04 can consume the fixture
interfaces; it must retain the unmet real-provider gate. Phase 05 owns baseline
search/confirmation; phase 06 owns regression bundles.
