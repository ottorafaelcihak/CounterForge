# Architecture (operational view)

CounterForge runs as a local Docker Compose stack on loopback only:
PostgreSQL 16 (durable evidence, jobs, usage, budgets, events), Redis 7
(best-effort RQ notification only), a one-shot migration job, the FastAPI
API, a single-concurrency worker, and the Next.js web app. PostgreSQL is
the source of truth; Redis loss never loses intent because queued jobs and
experiment state are recovered from PostgreSQL (`--recover-only`).

## Readiness

`GET /api/v1/health/live` is public liveness. Owner-only
`GET /api/v1/health/ready` returns structured booleans only — `database`,
`migrations`, `redis`, `artifacts`, `worker_heartbeat` — with no URLs,
paths, or secrets. The worker refreshes an exact queue-specific Redis
heartbeat (`counterforge:worker-heartbeat:<queue>`, 15 s TTL, refreshed
every 5 s) even while idle, so heartbeat freshness is bounded. Neither
probe performs provider connectivity checks or inference; provider smoke
is a separate, explicitly gated command (`make smoke-meta`).

Compose healthchecks mirror this without credentials on command lines:
API liveness over loopback HTTP, worker health from its own heartbeat
key, and web health from a local fetch. Services restart
`unless-stopped` (the migration job never restarts) and logs are bounded
(`json-file`, 10 MB × 3 per service).

## Evidence and recovery invariants

Artifacts are immutable, content-addressed, and referenced from
PostgreSQL. Startup recovery never drops evidence and never bypasses the
recovered unknown-charge/review gates: dispatched work whose lease
expires becomes `REVIEW_REQUIRED` with `UNKNOWN_CHARGE`, not a blind
retry. Queued export intent is a durable `background_jobs` row; a Redis
reset confined to the owning queue/database is recovered by re-enqueueing
from PostgreSQL.

Backup/restore, retention, latency measurement, and the disposable
rehearsal are documented in the [operations runbook](operations/runbook.md).
Security boundaries are in [security.md](security.md).


## Public snapshot boundary

Owner-only preparation selects approved findings and binds their complete export
eligibility evidence (sealed human review, observations, canonical confirmation
job) and image hashes to a sanitized DRAFT snapshot. Preparation grants no public
access. Explicit owner publication with intent and redistribution acknowledgement
activates one snapshot; anonymous manifest/image reads recheck source bindings
and eligibility and fail closed on stale or missing evidence. Snapshot asset IDs
are fresh public identifiers, never private asset IDs. Fixture provenance stays
explicit; only a pinned LIVE source may claim real-provider provenance.
Publication remains a separate Phase 10 approval boundary.

The historical `public_asset_whitelist` table remains for retained schema/data
compatibility and is inert: its rows grant no anonymous asset access.
`OWNER_LABEL` must stay stable for retained projects and published snapshots.
Recovery re-enqueues durable authorized work, so LIVE workers can resume billing;
stop workers or disable providers through restore/recovery reconciliation.
