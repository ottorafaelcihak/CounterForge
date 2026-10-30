# Phase 09 handoff

Phase 09 implements the owner product journey and local operational hardening.
Public demo preparation is available; the real retained snapshot remains a private
DRAFT. No public service, release or submission was published during this phase.

## Product and evidence

- Canonical project, seed, contract, target and mutation routes reuse the existing
  setup and evidence components. Project views load bounded pages of latest runs.
- Candidate filters retain INVALID, REVIEW_REQUIRED and UNKNOWN evidence. Stored
  counts, denominators, spend categories and report references come from the API.
  Finding views include mask/diff controls, original evidence downloads, refinement
  and version retest history.
- Authenticated fetch streaming refreshes the authoritative snapshot on reconnect,
  deduplicates event IDs and preserves terminal history. Session renewal retains
  unsaved work; Escape/button dismissal, visible expiry, keyboard focus and smaller
  viewports have browser coverage. Credentials stay out of URLs and public props.
- The anonymous gallery uses separate, narrow DTOs and fresh public asset IDs.
  Preparation retains source/report/image hashes and grants no anonymous access.
  Publication requires the exact manifest hash, explicit intent and redistribution
  acknowledgement. Manifest and image reads recheck bindings and complete export
  eligibility: human review, retained accounted observations and completed canonical
  confirmation. Fabricated counters or missing evidence cannot qualify a finding.
  The historical `public_asset_whitelist` table is retained but grants no access.

## Recorded demo preparation

The coordinator restored a hash-verified copy of the existing Phase 07 evidence,
verified all 21 referenced blobs, applied the final migrations and prepared a new
private DRAFT with providers DISABLED and queue dispatch off. It has real-provider
provenance, zero confirmed findings and one selected source image. It explicitly
states that no confirmed finding was discovered in that sample.

Anonymous manifest reads remained EMPTY and the draft image returned 404. Usage
rows remained 6 and job rows remained 1 before/after preparation; no paid work was
created. The original evidence was unchanged. A final prepared PostgreSQL backup
and blob hashes are retained in the ignored coordinator review directory; backup
SHA-256 is `7393b262fbd4ec2b95ea0e092b8b09b7be4aa4ee607fd840775492c309856acc`.
This historical private-schema backup used the verified PostgreSQL procedure,
not the new public-schema-only operations CLI.

This preparation does not supply redistribution approval or publication intent.
Phase 10 must obtain those approvals for the exact snapshot before publishing.
Fixture gallery tests publish only to isolated disposable test services and stay
prominently labeled synthetic; they establish no real Meta-model failure.

## Local operations

- Owner-only readiness distinguishes database, migration head, Redis, artifact
  access and a fresh queue-specific worker heartbeat. Startup/health perform no
  provider smoke. Compose uses loopback ports, bounded 10 MB × 3 logs and
  conservative restart policies.
- Backup uses one exported PostgreSQL snapshot and hashes every referenced unique
  blob. All artifact registration rows remain intact, including shared-blob aliases.
  Manifest validation requires the complete canonical table inventory. The CLI
  supports a dedicated current CounterForge database in `public` only; non-public,
  mixed/incomplete sources and DSN query parameters fail explicitly.
- Restore requires `CONFIRM_DISPOSABLE=1`, an empty artifact destination and no
  relations in any user schema. It verifies all table counts, unique blob counts
  and file hashes. Keep writers/workers stopped through reconciliation. Recovery
  re-enqueues authorized work and LIVE workers can resume billing.
- Retention is configurable, manual and dry-run by default. Referenced evidence is
  never swept. See the [runbook](../operations/runbook.md) for exact commands.

The isolated fixture rehearsal built the API/worker/web images (including
PostgreSQL 16 client tools), initialized clean volumes and reached readiness.
A registered artifact survived API/worker restart with its hash and project,
artifact and audit counts unchanged. Both gated operational tests passed: durable
export intent survived Redis loss and backup/restore, aliased blob rows were
preserved, a restored known bundle verified, and ambiguous dispatched work remained
held for review. The rehearsal removed only its own unique Compose project and
volumes.

Read-only latency was measured locally with 20 samples per endpoint on this small
fixture deployment:

| Endpoint | Observed p95 | Target |
| --- | ---: | ---: |
| `/api/v1/health/live` | 1.65 ms | 500 ms |
| `/api/v1/health/ready` | 11.78 ms | 500 ms |
| `/api/v1/projects` | 3.26 ms | 500 ms |

These are observed local GET timings, not production load measurements or a
performance guarantee. No model call is included. Anonymous manifest/image latency
was not measured; those paths revalidate full retained evidence on each request.

## Verification

Final coordinator checks, with live Meta calls disabled:

| Gate | Result |
| --- | --- |
| Lint/format | Ruff 220 files and ESLint passed |
| Types | mypy 115 sources and TypeScript passed |
| Generated contracts | Regeneration matched committed-source output |
| Unit | 245 passed |
| Contract | 173 passed, including locally mocked SDK subprocesses |
| Client | 13 named Node tests passed |
| Production web build | Passed |
| Database | 280 passed, 2 intended Docker-only skips |
| Browser | 15 passed in the final complete run |
| Docker operations | Both gated tests passed; restart/hash and cleanup checks passed |
| Export verifier | Healthy passed; broken/replay/tamper/optimized cases failed as intended |

The complete browser journey uses a non-default object label, five reviewed masks,
a locked contract, a target, three correct baseline replies, a reviewed candidate,
three fresh incorrect confirmations and a verified export. The replies are
LOCAL_FIXTURE. Retained 1600×900 finding/cockpit captures and mobile captures are
kept with hashes in the ignored coordinator review directory.

Codex (`gpt-6.1-sol`) implemented the owner UI and final fixes; Muse
(`muse-wonder-1.0`) implemented snapshot/operations integration and checked
acceptance coverage; Claude Code (`claude-opus-5`) independently reviewed the UI,
completed change and final fixes. Codex reached its account limit after writing
the final fix report; the coordinator completed and verified the remaining test
fixture corrections. Independent scoped re-review approved all four blocking fixes
and found no new blocking regression; all final coordinator gates passed.
New CounterForge Meta API test usage in Phase 09: **0 calls, USD 0 actual and USD 0
unknown charges**. This excludes coding-agent subscriptions/inference.

## Remaining gates and limits

- Phase 08's measured campaign still requires independent human dataset counts,
  redistribution approval and a separate live-call allowance. Its generator labels
  have not been promoted to human truth. No adaptive lift, target discovery rate,
  audit precision or genuine stable real-model failure is claimed.
- Publication/rights acknowledgement, external access/TLS checks, release acceptance
  and submission remain Phase 10 actions. Keep OWNER_LABEL stable for retained
  projects and snapshots.
- Saved SSE events exceeding the 1 MiB client limit still need an event-history
  fallback. Current payloads are curated; the deferred fallback never licenses
  marking an active run finished. Real assistive-technology and production-load
  testing remain outside the local browser/rehearsal evidence.

See [architecture](../architecture.md), [security](../security.md) and
[Phase 10](plan-10.md) for the release boundary.
