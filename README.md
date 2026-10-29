# CounterForge

Find the failure. Prove the weakness. Prevent the regression.

CounterForge is a visual-model reliability laboratory for controlled image changes,
independent ground-truth checks, repeated evaluation and portable regression tests.
The initial task is single-image object counting.

Phases 01–04 implement typed contracts, PostgreSQL evidence storage, atomic spend
accounting, bounded Meta adapters, durable setup jobs and an owner workspace for
seed upload, target checks and audited mask correction, plus controlled mutations,
independent verification and candidate review. Experiment scheduling and regression
ZIP creation belong to the following phases.
Live entitlement and the real-provider smoke gate remain **unmet**; fixtures do
not establish live access or a real model weakness. Phase 04 specimens have only
simulated acceptance decisions; actual human acceptance and automatic threshold
calibration remain outstanding.

## Development setup

Requires Python 3.12, uv 0.13.6 or compatible, Node.js 24 LTS and npm. Docker
Compose supplies PostgreSQL 16, Redis 7, the API, worker, web app and persistent
artifact storage.

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
when a live-test flag is inherited. After changing canonical contracts, run
`make generate-contracts`, review the generated diff and run `make check-contracts`.

## Run local services

Copy `.env.example` to an ignored `.env` and configure `POSTGRES_PASSWORD` and a
strong `OWNER_AUTH_TOKEN`, or export the values explicitly. Compose reads its
environment file; Python services read process environment without loading dotenv
files. The web service receives the owner token on its server only.

```bash
make compose-check    # validates without printing resolved configuration
make services-up      # builds, migrates and starts the local stack
# Open http://localhost:3000 and sign in with the configured owner access token.
make services-down    # retains persistent volumes
```

All published ports bind to loopback. Override `POSTGRES_PORT`, `REDIS_PORT`,
`API_PORT` or `WEB_PORT` as needed. Set `WEB_ORIGIN` to the exact browser origin,
default `http://localhost:3000`. `OWNER_LABEL` scopes projects; changing it changes
which projects this owner can access.

For host development, export the PG components or a fully expanded
`postgresql+psycopg` `DATABASE_URL`, plus `REDIS_URL`, `ARTIFACT_ROOT`,
`OWNER_AUTH_TOKEN`, `OWNER_LABEL`, `WEB_ORIGIN` and `API_INTERNAL_URL`. Then run
`make migrate`, `make dev-api`, `python -m services.worker.app.main` and
`make dev-web` in separate terminals. Use `ENQUEUE_JOBS=1` for API job notification.
`API_INTERNAL_URL` defaults to `http://127.0.0.1:8000` for the Next server.
Nested variable interpolation is rejected; component passwords remain literal data.

The API docs are at `http://127.0.0.1:8000/docs`. Business routes require
`Authorization: Bearer <OWNER_AUTH_TOKEN>`. The browser uses an opaque, eight-hour
HttpOnly/SameSite=Strict session and an allowlisted same-origin proxy. Mutations
require the configured Origin; upstream owner/model keys never become browser
DTOs, URLs or local-storage values. Public asset reads require an explicit server
whitelist; the public demo belongs to a later phase.

## Prepare and review a seed

Create a project, upload a rights-cleared image, enter the counting question and
human count, and name its label reviewer. JPEG, PNG and single-frame WebP inputs
are limited to 10 MiB and 16 megapixels. A bounded subprocess applies EXIF
orientation, composites alpha on white and creates a metadata-free RGB PNG.
Original bytes/SHA, normalized SHA and pixel SHA are retained separately. Upload
filenames never determine storage paths.

Configure a curated `META_SPARK` or synthetic `LOCAL_FIXTURE` target. A target
check is one handshake; it is not the full seed baseline from phase 05. Request
segmentation with explicit call, dollar and runtime limits, then inspect each
instance over the source image. Paint, erase or use source-coordinate rectangles
to correct masks; add missing instances or retire extras. Corrections create new
immutable assets, retain old approval evidence and clear approval on the current
revision. Mask PNGs must be binary, nonempty and exactly match the source geometry.

Approve each exact current mask and acknowledge any detection/count discrepancy.
The human count never changes automatically. A new VTC requires one distinct
approved mask per expected object. Locked historical contracts and started runs
retain their precise asset revisions; a new run rechecks current review and source
integrity. Lists use signed scoped cursors with at most 100 items.

## Generate and review a candidate

Use the authenticated API to create an experiment intent with an approved VTC
and target, then `POST /api/v1/experiments/{id}/mutations` with an
`Idempotency-Key`, typed `plan` and `rng_seed`. Supported families are
`background_replacement`, `background_clutter` and `brightness_shift`.
The Phase 05 experiment scheduler is not enabled yet.

For a generative background, first request
`POST /api/v1/contracts/{id}/backgrounds` with the generative plan and explicit
finite job limits. After that job succeeds, supply its `background_job_id` with
the same plan when creating the candidate. Original approved foreground pixels
are copied exactly onto the normalized background. Candidate segmentation via
`POST /api/v1/mutations/{id}/segment` is optional supporting evidence and uses the
same bounded setup-job accounting. Preparation usage is separate from experiment
spend until Phase 05 integrates orchestration.

Open `/mutations/{id}` in the owner workspace to inspect original, candidate,
background, protected union, pixel diff, source masks, recipe and revision history.
New candidates require human semantic and visibility decisions. A required FAIL
means INVALID; an unresolved required check means REVIEW_REQUIRED. SAM agreement
cannot approve a candidate. Confirmed visibility destruction is INVALID;
ambiguous visibility stays REVIEW_REQUIRED.

Review binds the current report and exact evidence hashes. A stale decision is
rejected; reload and review again. Resolving a prior human rejection requires an
explicit ground-truth audit with notes and named failed checks. Structural failures
cannot be overridden. Identical retained candidate images within one experiment
share the original recipe and review history, including rejections.

Recipes record exact parameters and runtime versions. Strength is specific to
each family: brightness `0.5` is the identity, replacement `0` is the identity,
and clutter `0` already draws one rectangle. It is not a universal severity scale.
See the [Phase 04 handoff](docs/plans/phase-04-handoff.md) for integration rules.

## Provider modes and offline fixtures

`PROVIDER_MODE=DISABLED` is the default. Manual seed and mask review work without
provider dispatch. Live mode additionally requires `RUN_META_LIVE_TESTS=1`, a
server-only `MODEL_API_KEY`, finite job limits and an unexpired operator allowance.
SDK construction and curated model names do not prove entitlement.

For a synthetic development session, set `APP_ENV=development`,
`PROVIDER_MODE=FIXTURE` and `ALLOW_SYNTHETIC_FIXTURES=1`, then recreate the services.
The workspace can load the original CC0 development image with five separate blue
rectangles. Only its known SHA is supported by wire fixtures. SAM intentionally
returns four proposals so the missing-instance workflow is exercised. All output
is labeled synthetic; the official parser and SDK still execute locally.

No deployment allowance exists by default. Configure a finite zero-dollar fixture
allowance explicitly:

```bash
docker compose exec api python -m scripts.configure_job_allowance \
  --max-usd 0 --max-calls 100 --expires-in-minutes 60 --actor local-owner
docker compose exec api python -m scripts.configure_job_allowance --show
```

Each provider attempt reserves and commits, claims and commits before wire I/O,
then reconciles in a fresh transaction. A PostgreSQL session lease limits global
provider concurrency to one without holding a SQL transaction across network I/O.
Actual usage, conservative estimates and unresolved charges remain separate.
Missing usage is not priced at zero. Ambiguous post-send failures retain their
upper-bound charge and are never automatically retried. Only proven connection
failures and explicitly authorized unbilled rejections can receive bounded retries.

Allowance updates retain consumed totals. Lower ACTUAL costs do not refund the
shared conservative upper bound; higher costs increase it. Undispatched voids and
trusted UNBILLED proofs release reserved dollars once. Pre-send proofs release
call capacity too; a sent unbilled 429 still consumes a call.

PostgreSQL is the source of job intent. Redis notification occurs after commit;
a Redis failure leaves a queued record. Startup recovery and `make recover-jobs`
rescan queued/expired setup jobs. Duplicate deliveries do nothing. Expired
undispatched work can be requeued; dispatched ambiguous work and lost settled
output require review and a deliberate new job. RQ carries a UUID-only JSON
payload and has no automatic provider retry policy.

## Explicit live smoke gate

Read [the provider record](docs/plans/phase-03-provider-record.md) before live
configuration. The fixed API origin is `https://api.meta.ai/v1`; current standard
SKUs are Spark 1.3, Image 1.0 and SAM 3.1. Pricing comes from root `pricing.toml`.
No generated-image CDN host has been verified, so URL downloads fail closed by
default and base64 output is requested.

`make smoke-meta` requires `PROVIDER_MODE=LIVE`, `RUN_META_LIVE_TESTS=1`, a model
key, an existing operator allowance, explicit finite `SMOKE_META_MAX_USD`,
`SMOKE_META_MAX_CALLS` (at least four), `SMOKE_META_MAX_RUNTIME_SECONDS`, and owned
`SMOKE_META_SEED_ID`, `SMOKE_META_VTC_ID`, `SMOKE_META_TARGET_ID` references with
current reviewed evidence. It exercises planner, SAM, image and target sequentially
through the same accounting. It records sanitized evidence IDs and usage. No live
smoke was run during phase 03 implementation; TS-AT-18 remains unmet.

## Verification and maintenance

Provision disposable loopback services and opt in explicitly:

```bash
export COUNTERFORGE_TEST_DATABASE_URL=postgresql+psycopg://counterforge_test@127.0.0.1:55432/counterforge_test
make test-db
export COUNTERFORGE_TEST_REDIS_URL=redis://127.0.0.1:56379/15
uv run --locked python -m playwright install chromium
make build-web
make test-e2e
```

Database tests create and drop only their random schemas, retaining test `pgcrypto`
in `public`. Browser verification creates its own schema, queue, artifact root,
service processes and zero-dollar fixture allowance, then cleans up those owned
resources. It checks five-object seed setup, candidate verdicts, explicit audits,
stale evidence, missing images, revision history, session renewal, privacy, CSRF
and mobile overflow. It never
falls back to application `DATABASE_URL` or flushes shared Redis data. CI runs
unit, contract, database and browser suites with live Meta calls disabled.

Orphan maintenance defaults to a dry run and a minimum 24-hour age. Referenced
assets are retained; registration and maintenance share a transaction lock:

```bash
docker compose exec api python -m scripts.maintain_artifacts --dry-run
# After inspecting the report:
docker compose exec api python -m scripts.maintain_artifacts --delete
```

Migration `0001` executes the supplied SQL byte for byte. `0002` adds workflow and
budget support; `0003` adds explicit billing provenance, job fencing and append-only
mask history; `0004` adds immutable candidate evidence, request aliases and ordered
append-only validation history. Downgrades refuse to discard evidence. Asset reads verify paths,
size and SHA; content-addressed blobs are fsynced and atomically installed before
transactional registration.

## Project structure

```text
apps/web/          Owner setup, mask editor, candidate evidence and API types
services/api/      Authenticated API, database, artifacts and accounting
services/worker/   Durable setup jobs, fixture transport and recovery
packages/          Shared contracts, adapters and later-phase boundaries
fixtures/phase03/  Original CC0 development seed and manifest
scripts/           Contract generation, smoke and operator maintenance
tests/             Offline, PostgreSQL and real-process browser checks
tests/fixtures/phase04/  Labeled synthetic candidate evidence specimens
docs/plans/        Ordered phases and verification handoffs
```

Start with the [documentation index](docs/README.md), [roadmap](docs/plans/README.md)
and [phase 04 handoff](docs/plans/phase-04-handoff.md). The PRD, technical
specification and supplied SQL remain the source documents.
