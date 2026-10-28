# Phase 02 handoff — persistence and API foundation

Verified locally on October 28, 2026. See [plan-02.md](plan-02.md) for scope and
[README](../../README.md) for setup and operator commands.

## Delivered

- Exact supplied PostgreSQL baseline installed by Alembic `0001`; additive
  `0002_workflow_support` adds durable jobs, seed provenance, VTC evidence
  snapshots, contract immutability, operation quantities/dispatch claims,
  pre-experiment usage links, public asset whitelist and a shared allowance.
- Transaction-bound SQLAlchemy Core tables/repositories shared by API and worker.
  PostgreSQL remains authoritative; a Redis/RQ worker is available but no
  application provider dispatch is enabled.
- Bounded image decoding and content-addressed, atomic artifact storage with
  original-byte evidence SHA, normalized output/pixel SHA, verified reads and
  explicit orphan maintenance.
- Owner-authenticated project/seed/mask/contract/target/experiment/job/finding and
  regression reads/setup. Immutable VTC revisions and run snapshots enforce seed,
  project, approved-mask and limit associations. Queued exports retain the same
  public UUID when their verified assets are registered.
- Decimal accounting with serialized dollar/call/candidate ceilings, runtime/state
  checks, global operation-key idempotency, single dispatch claims, reconciled
  usage, conservative unknown charges and finite pre-experiment allowances.
- Persistent loopback Docker Compose, non-root API/worker images, generated
  OpenAPI/TypeScript, PostgreSQL CI checks, and the preserved three-provider
  workflow in AGENTS.md.

## Verification evidence

| Check | Result |
| --- | --- |
| `make test` | 49 unit + 70 contract tests passed; outbound sockets disabled |
| `make test-db` with explicit disposable loopback test URL | 69 real PostgreSQL integration tests passed |
| `make check-contracts` | Eight generated schemas and TypeScript match; no provider calls |
| `make lint` | Ruff checks/format and ESLint passed |
| `make typecheck` | mypy: 51 source files; TypeScript passed |
| `make build-web` | Next.js production build passed |
| Docker API/worker/migration images | Locked production dependency installation and builds passed |
| Compose config/up | Quiet config validation; fresh database migrations, healthy PostgreSQL/Redis, API and idle worker |
| HTTP smoke with synthetic JPEG | Public live 200, private unauthenticated 401, authenticated ready 200, project/upload 201, source SHA preserved, normalized image 200, segmentation 202, durable queued job/read/list 200 |
| Restart persistence | Project, seed and QUEUED job survived API/worker restart; ready 200 |
| Operator commands | Orphan dry run: zero stale files; fresh deployment has no job allowance |
| Baseline SQL | Documentation and migration copies match byte for byte |

Baseline SHA-256:

```text
671a67557e90e15e7a0905a510c1026984caab7aeff994b56fc3ee1b657c596c
```

Fresh Compose database inspection:

```text
PostgreSQL 16.15
Alembic head: 0002_workflow_support
Tables (21 including alembic_version):
  alembic_version, artifact_assets, audit_events, background_jobs,
  deployment_allowance, experiment_events, experiments, findings,
  model_observations, mutation_plans, mutation_validations, mutations,
  projects, public_asset_whitelist, regression_exports, run_operations,
  seed_cases, seed_masks, target_configs, usage_events, visual_contracts
```

Regression cases were observed failing before their corresponding fixes. Coverage
includes upload/header/decode/stream bounds, tampering and symlinks, orphan
registration races, identical blobs in different evidence roles, revoked mask
approval, protected-label association, original evidence integrity, immutable
revisions, current approval snapshots, sanitized readable audits, truthful metrics,
concurrent caps/claims, cross-scope key races, changed settlement metadata,
pre-experiment gating/expiry, allowance overshoots and unknown-charge handling.
These use synthetic evidence, not real model measurements. The final combined
suite passed **188/188** after the audit-redaction security fix. The final source
review accepted the material fixes; its remaining availability minor is recorded
below.

## Decisions and costs

1. Missing entities return 404; cross-project references return 403. The configured
   owner label scopes records. Changing that label may require an explicit
   ownership migration; status-sensitive clients must follow these semantics.
2. Reject animated inputs. Normalize EXIF orientation, composite alpha on white,
   output RGB PNG and strip metadata/ICC without color conversion. Ignore client
   filenames. Color-managed workflows need a future normalization policy revision;
   the original bytes remain authoritative evidence.
3. Every **new** experiment start rechecks current approval, mask label and source
   integrity under locks. Contract revisions and prior run snapshots remain
   immutable. Retired/revised masks require fresh approval for a new run; replaying
   an existing start key returns that existing run.
4. One immutable blob can serve distinct evidence roles. Additive `0002` changes
   storage uniqueness to `(storage_key, kind)`; each role has its own asset UUID.
   Baseline `0001` is unchanged. A downgrade fails safely if the old single-role
   uniqueness cannot be restored without losing evidence.
5. Reserve and commit intent, then claim and commit **before** network I/O; only
   a successful claim may dispatch. Unknown charges consume the upper bound and
   block automatic retries/refunds. Their stored reason is a fixed safe code,
   rather than arbitrary provider exception text. Detailed transport diagnostics
   require a separate secure mechanism in the adapter phase.
6. Pre-experiment jobs need explicit owner limits plus a finite, unexpired shared
   allowance. The allowance retains conservative consumption on lower settlement,
   adds higher actual costs exactly once, and refunds only a never-dispatched
   void. Operators may need to explicitly extend conservative allowances.
7. Phase 02 persists durable intent without scheduling providers. Pricing is
   explicitly unconfigured in run snapshots. Phase 03 must verify current provider
   interfaces/pricing and enforce these transaction boundaries before wire calls.

## Three-provider participation

Used the installed `develop-with-agent-team` bridge, serializing edit ownership in
the current master checkout:

| Harness / actual model | Contribution |
| --- | --- |
| Codex / `gpt-6.1-sol` | DB/migration/UOW/Compose implementation and focused checks; initial capacity failure recovered with one explicit configured-model retry |
| Claude Code / `claude-opus-5[1m]` | Correctness preflight and accounting implementation |
| Muse / `muse-wonder-1.0` | Acceptance/security preflight, completed evidence API review, accounting review and final fix review |
| Coordinator | API/artifact integration, independent failing regressions, material review fixes and all final executable verification |

Claude's noninteractive harness could edit but its command/inbox execution required
approval, so it could not independently run its tests or read follow-up feedback.
The coordinator verified and corrected that source. Muse reviews were source-only;
parent-supplied test evidence is not an independent Muse test run. Fresh bridge
1.1.0 CLI liveness/configuration took precedence over the stale MCP connection.
Development harness calls are separate from the application; no paid CounterForge
provider inference, production deployment or public publication was performed.

## Deferred minor

Muse identified a fail-closed public-whitelist edge case outside the single-owner
flow: a valid token scoped to a different owner label receives 403 for an image
that an unauthenticated viewer can read through the whitelist. Signing out uses
the public path. No private evidence is exposed. Revisit the owner-or-public
fallback with multi-owner/public gallery work in Phase 09.

## Phase 03 integration requirements

Consume durable background job UUIDs and sanitized ID/settings requests. Implement
provider adapters and job claiming/dispatch only after verifying supported wire
formats and conservative pricing. Use separate committed reservation, dispatch
claim and reconciliation transactions. A crash after claim with an uncertain
outcome must remain reviewable and must never trigger a blind paid retry.

SAM mask production/correction and the setup UI arrive in Phase 03. The current
mask approval API works on persisted mask proposals; Phase 02 does not fabricate
proposals. Experiment starts persist `BASELINE_VALIDATING`, with
`provider_dispatch_enabled=false`. Scheduling, events/control actions, mutation
execution/review, confirmations, complete metric endpoints, public demo and ZIP
building remain explicitly unimplemented in their owning phases. Existing read
DTOs report recorded evidence/usage without inventing progress or findings.

The Git workflow remains master-only for MVP. The removed AGENTS-only local commit
is replaced by this phase's combined implementation/workflow commit; no history
rewrite of the remote was required.
