# CounterForge

Find the failure. Prove the weakness. Prevent the regression.

CounterForge is a visual-model reliability laboratory for controlled image changes,
independent ground-truth checks, repeated evaluation and portable regression tests.
The initial task is single-image object counting.

Phases 01–02 provide typed contracts, offline fixtures, PostgreSQL persistence,
verified image artifacts, owner-authenticated setup/read APIs and atomic budget
accounting. Segmentation, target checks and exports have durable queued records.
Provider dispatch, experiment scheduling, ZIP creation and the product UI arrive
in later phases; a queued job does not execute yet. No live discovery is claimed.

## Development setup

Requires Python 3.12, uv 0.13.6 or compatible, Node.js 24 LTS and npm. Docker
Compose supplies PostgreSQL 16, Redis 7 and persistent artifact storage.

```bash
uv sync --locked
npm ci --prefix apps/web
make check-contracts
make lint
make typecheck
make test
make build-web
```

`make test` runs unit and contract checks with outbound sockets disabled, even
when `RUN_META_LIVE_TESTS=1` is inherited. It never calls billable endpoints.
After changing canonical contracts, run `make generate-contracts`, review the
OpenAPI/JSON Schema/TypeScript diff, then run `make check-contracts`.

## Run local services

Copy `.env.example` to an ignored `.env` and set a local `POSTGRES_PASSWORD` and
strong `OWNER_AUTH_TOKEN`, or export those two variables in your shell. Compose
reads its environment file; Python applications read only explicit process
environment and never load dotenv files. Do not commit credentials.

```bash
make compose-check    # validates without printing resolved configuration
make services-up      # builds, migrates, starts API and idle RQ worker
make dev-web          # Next.js shell at http://127.0.0.1:3000
make services-down    # stops services, retains persistent volumes
```

Published database, Redis and API ports bind to `127.0.0.1`. Override
`POSTGRES_PORT`, `REDIS_PORT` or `API_PORT` if needed. Configure `WEB_ORIGIN` as
one exact HTTP(S) origin, default `http://localhost:3000`, matching the browser's
address. `OWNER_LABEL` defaults to `local-owner` and scopes projects; changing it
changes which projects the configured owner can access.

The API is at `http://127.0.0.1:8000/docs`. `/api/v1/health/live` is public;
`/api/v1/health/ready` requires the owner token and checks migration heads,
PostgreSQL, Redis and an artifact
write/read/delete without model calls. Business routes require
`Authorization: Bearer <OWNER_AUTH_TOKEN>`. Public asset reads require an explicit
server-side whitelist entry; the public demo is a later-phase endpoint.

Host execution uses exported `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`,
`PGPASSWORD`, `REDIS_URL`, `ARTIFACT_ROOT`, `OWNER_AUTH_TOKEN`, `OWNER_LABEL` and
`WEB_ORIGIN`. A fully expanded PostgreSQL/psycopg `DATABASE_URL` takes precedence
over PG components. Nested variable interpolation is rejected; component
passwords are literal data. Then run `make migrate` and `make dev-api`.

## Evidence and API behavior

Create/read/list projects and seeds; uploads require a question, expected count,
rights acknowledgement and named human label reviewer. JPEG, PNG and single-frame
WebP are limited to 10 MiB and 16 megapixels. The decoder runs in a bounded
subprocess. EXIF orientation is applied, alpha is composited on white, RGB PNG
output strips metadata and ICC profiles without color conversion. Provenance keeps
original bytes/SHA-256 separately from normalized output and pixel hashes.
Upload filenames never determine storage paths.

Content-addressed files are fsynced and atomically installed before transactional
registration. Identical bytes share a blob; distinct evidence roles retain their
own asset UUIDs. Reads verify path, size and SHA. Project ownership and exact seed
mask associations are enforced in services. Mask review records sanitized human
rationale. Contracts require approved masks for the protected label, canonical
JSON hashes and immutable increasing revisions. Every new experiment rechecks
current approval/source integrity and locks its chosen contract and settings;
already-started snapshots retain their historical evidence.

Target configuration accepts curated `META_SPARK` or `LOCAL_FIXTURE` adapters,
with server-controlled secret references. External checks stay queued. Experiment
starts require `Idempotency-Key`; replaying changed content is a conflict. Request
limits may only tighten the contract ceilings (defaults: 20 candidates, 60 target
calls, $10 estimated, 1200 seconds). Lists use signed scoped cursors, at most 100
items. DTOs omit secret references, raw provider payloads and storage paths.
Pending export UUIDs remain the same upon completion; downloads require verified
bundle/manifest assets. ZIP generation itself belongs to Phase 06.

## Accounting and maintenance

`Budget` is shared by API/worker. Reserve inside a unit of work and **commit**;
claim with `begin_operation` in a second transaction and **commit before network
I/O**. Only the caller receiving `True` may dispatch. Settle reconciled Decimal
costs or record an unknown charge in a new transaction. Never retry an ambiguous
charged call automatically. Unknown charges consume the upper bound and require
review; known costs above the bound are recorded in full and stop further work.
Only an undispatched intent may be voided. Runtime/state are rechecked at claim.

Pre-experiment jobs also require explicit finite request `limits` and a shared
operator allowance. No allowance exists by default. Configuring one does not
execute a job or enable provider adapters:

```bash
docker compose exec api python -m scripts.configure_job_allowance --show
# Explicit local authorization example, when paid adapters are implemented:
docker compose exec api python -m scripts.configure_job_allowance \
  --max-usd 0.50 --max-calls 2 --expires-in-minutes 60 --actor local-owner
```

Allowance updates retain consumed totals. Lower actual costs do not refund the
shared conservative estimate; higher costs increase it. Only never-dispatched
voids release the reserved amount/calls.

Orphan maintenance defaults to a dry run and a minimum 24-hour age. Registration
and maintenance share a transaction lock so maintenance cannot remove a blob
being registered. Referenced evidence is retained:

```bash
docker compose exec api python -m scripts.maintain_artifacts --dry-run
# After inspecting the report:
docker compose exec api python -m scripts.maintain_artifacts --delete
```

Migration `0001` executes the supplied SQL byte for byte; `0002_workflow_support`
adds jobs, provenance/snapshots, immutable contract protection, reservations and
shared allowance. Baseline downgrade refuses to destroy evidence.
`create_uow_factory(engine)` reflects migrated tables once. `with factory() as uow`
exposes `connection`, source-named `tables` and `background_jobs`; exit rolls back
unless `uow.commit()` is called explicitly.

## Database verification

Provision a **disposable loopback PostgreSQL database**, then explicitly opt in:

```bash
export COUNTERFORGE_TEST_DATABASE_URL=postgresql+psycopg://counterforge_test@127.0.0.1:55432/counterforge_test
make test-db
```

Each test creates a random schema and drops only that schema. Tests never fall
back to `DATABASE_URL`. Bare `pytest` skips integration cases without the test
URL; `make test-db` fails clearly when it is absent. Database cases allow only
loopback sockets. CI provisions a disposable PostgreSQL service and runs both
suites; live Meta calls remain disabled.

## Project structure

```text
CounterForge/
├── apps/web/             # Next.js shell and generated API types
├── services/api/         # Authenticated API, DB, artifacts and accounting
├── services/worker/      # Idle RQ consumer and shared budget exports
├── packages/             # Six Python workspace packages and shared contracts
├── scripts/              # Contract export, allowance and artifact maintenance
├── tests/                # Unit, contract and disposable PostgreSQL checks
└── docs/plans/           # Ordered phases and verification handoffs
```

The offline `LOCAL_FIXTURE` target uses image hashes and explicit expected counts.
Unknown images/replies stay UNKNOWN; fixtures never prove a real AI weakness.

Start with the [documentation index](docs/README.md), [roadmap](docs/plans/README.md)
and [Phase 02 handoff](docs/plans/phase-02-handoff.md). The PRD, technical specification
and supplied SQL remain the source documents.
