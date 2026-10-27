# Phase 09 — Product Completion and Operational Hardening Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Build on the existing working setup, cockpit and export screens.

**Goal:** Complete the product journey and provide a stable local deployment with a sanitized read-only demo surface.

**Architecture:** Next.js consumes generated API types and same-origin evidence routes. Owner controls remain private; public data is an explicit whitelisted snapshot, never a generic proxy into live experiments. Compose persists evidence and exposes only intended services.

**Tech stack:** Next.js/React/Tailwind, openapi-typescript, FastAPI/SSE, Playwright, Docker Compose and existing observability/logging tools.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§8, 13–15, 17–18, 24; [PRD](../CounterForge_PRD_v1.0.md) FR-13, FR-18, §§11–12, 19. Acceptance: TS-AT-15–16; supports PRD-AT-09, PRD-AT-12 and earlier browser gates.

**Prerequisite:** [Phase 08](plan-08.md). **Next:** [Phase 10](plan-10.md). **Target:** November 3, 2026.

## Global constraints

- Complete the specified routes/screens; reuse canonical DTOs and state enums rather than parallel frontend models.
- UNKNOWN, REVIEW_REQUIRED, intermittent and budget-stop labels remain explicit and accessible.
- Public users cannot submit experiments, invoke target checks, review private images or download unapproved artifacts.
- Bind local ports to `127.0.0.1`; use existing SSH/Tailscale access for private controls.
- Do not change unrelated workstation/RDP/desktop/agent services. Remote exposure needs TLS/reverse-proxy controls and the required authorization.

## Review focus

- Reconnection and duplicate events must not show stale success or double counts.
- Empty/error/review-only runs still need a usable interface.
- Public identifiers must not enumerate private evidence.
- Keyboard, contrast and uncertainty information must survive visual polish.
- Restart policies and health probes must not cause unexpected paid traffic.

## Tasks

### Task 1: Complete dashboards, rejection explorer and regression library

**Files:** Web dashboard/project/candidate/finding/regression routes and reusable evidence components; browser specs.

**Interfaces:** Use §13's routes and phase 02's list/read additions. `GET /api/v1/experiments/{id}/metrics` supplies stored counts, denominators, spend categories and report references, not frontend-recomputed findings.

- [ ] Add project overview/latest runs, complete candidate filters, hypothesis history, validity/rejection detail and regression retest history to the existing MVP screens.
- [ ] For FR-18 show the exact failed/unknown checks, policy revision, original/edited image and reviewer outcome. Preserve rejected attempts rather than filtering them out of the product narrative.
- [ ] Show stable/intermittent/not-reproduced/inconclusive findings with fresh counts; provide mask/diff toggles and original evidence downloads.
- [ ] Cover no projects, no findings, failed export, missing key, provider quota, interrupted run and held-for-review states. Generated client errors expose safe correlation IDs and actionable status.
- [ ] Verify fixture browser scenarios plus one end-to-end setup-to-export flow; ensure display values reconcile to API fixtures.

### Task 2: Authentication-aware SSE and accessible interaction

**Files:** Web API/event client, authentication boundary, focus/dialog/forms and reconnection tests.

**Interfaces:** Private event requests use authenticated same-origin fetch streaming or a server-side bridge; tokens never appear in URLs. Snapshot refresh follows every reconnect, with event-ID deduplication.

- [ ] Test dropped streams, replayed events, terminal-state reconnect and owner session/auth expiry without polling paid endpoints.
- [ ] Keep owner credentials out of public page props/build artifacts. If cookies are introduced for a same-origin bridge, use HttpOnly/SameSite/Secure as appropriate and CSRF checks for writes; do not expose private dashboard controls to the internet as a shortcut.
- [ ] Complete keyboard navigation, visible focus, semantic labels, error announcements, image descriptions and accessible contrast. Avoid color-only validity or spend indicators.
- [ ] Verify responsive desktop and smaller viewport flows; capture a legible 16:9 finding/cockpit view for the demo.
- [ ] Run `make typecheck`, lint and relevant Playwright accessibility/interaction checks.

### Task 3: Whitelisted public demo snapshot

**Files:** Public demo DTO builder/route, `/demo` page and `tests/integrations/test_public_demo.py` plus browser tests.

**Interfaces:** `GET /api/v1/public/demo -> DemoManifest` returns only an explicitly approved recorded run, selected public asset IDs, sanitized metrics, architecture summary and limitations. Snapshot preparation is private; actual publication belongs to phase 10.

- [ ] Create an approved snapshot from real retained evidence; record publication intent in an audit record and retain the source experiment/report hashes.
- [ ] Apply an explicit asset/data whitelist. Public requests for other experiment IDs, private assets, usage headers or unapproved exports return a safe denial.
- [ ] Mark recorded/replay sections and real versus fixture provenance on screen. Remove any controls capable of dispatching paid work from the public page.
- [ ] Test guessed UUIDs, changed query parameters, public write attempts and private asset references. Assert zero provider dispatches across the complete anonymous gallery flow.
- [ ] Verify TS-AT-16 locally without exposing a public service; authorized publication is handled in the release phase.

### Task 4: Operational readiness and deployment rehearsal

**Files:** Compose/service restart settings, scripts/health checks, `docs/architecture.md`, `docs/security.md` and operational instructions.

**Interfaces:** Readiness reports migration status, database/Redis connectivity, artifact access and worker heartbeat. Explicit provider smoke remains a separately gated command.

- [ ] Test clean volume initialization, container restart and resumption/review of persisted work with fixture providers. No loss of uploaded assets, budgets, events or pending export intent is acceptable.
- [ ] Add bounded log retention and redaction, conservative service restart policies and explicit maintenance/backup/restore steps for PostgreSQL and artifacts. Keep 30-day local retention configurable; never delete active evidence through an automatic sweep.
- [ ] Verify restored DB/artifacts with known hashes and an existing bundle. A Redis reset must be recoverable from durable job/operation intent.
- [ ] Measure local read-only API latency separately from model calls; report observed p95 against the source's 500 ms target without claiming unmeasured performance.
- [ ] Run `make lint`, `make typecheck`, `make test-unit`, `make test-contract`, `make test-db`, `make test-e2e` and `make verify-export`; no default check may invoke a paid endpoint.
- [ ] Commit with `feat: complete evidence UX and harden demo operations` after recording browser/operational evidence.

## Exit criteria and evidence

- [ ] Complete owner journey works, including rejection, uncertainty, retest and export states.
- [ ] Reconnect/accessibility checks pass and display counts agree with stored reports.
- [ ] Anonymous demo tests prove isolation and zero spend-capable operations.
- [ ] Local restart/restore rehearsal passes with reproducible run instructions and hashes.

## Handoff

Phase 10 uses the prepared read-only snapshot, stable local product and preserved
evaluation figures. Remote publication and submission remain explicit release
actions rather than side effects of UI testing.
