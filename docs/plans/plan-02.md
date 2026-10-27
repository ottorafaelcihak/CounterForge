# Phase 02 — Persistence and API Foundation Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans`, or `superpowers:subagent-driven-development` when selected. Complete each task's checks before marking it done.

**Goal:** Persist trustworthy project evidence behind authenticated APIs and provide atomic accounting primitives before live provider calls.

**Architecture:** PostgreSQL is the source of truth. Redis carries job IDs, while immutable files live in a content-addressed local store. Repository services enforce project ownership, VTC revision locks and idempotency within database transactions.

**Tech stack:** FastAPI/Pydantic, SQLAlchemy, psycopg, Alembic, PostgreSQL 16+, Redis/RQ, Pillow and Docker Compose.

**Spec:** [SQL baseline](../CounterForge_PostgreSQL_Schema_v1.0.sql); [technical specification](../CounterForge_Technical_Specification_v1.0.md) §§3, 6–8, 14–15, 17–18; [PRD](../CounterForge_PRD_v1.0.md) FR-01–04, FR-14, AT-01 and AT-09. Technical tests: TS-AT-01, TS-AT-11, TS-AT-15.

**Prerequisite:** [Phase 01](plan-01.md). **Next:** [Phase 03](plan-03.md). **Target:** October 27, 2026.

## Global constraints

- Install the supplied schema unchanged as migration `0001`; preserve evidence foreign keys with `ON DELETE RESTRICT`.
- Images are limited to 10 MiB and 16 megapixels; support JPEG/PNG and WebP only with a supported decoder.
- Defaults: 20 candidates, 60 target calls, $10 estimated and 1200 seconds per experiment.
- API responses use canonical UUID strings, bounded cursor pagination (`limit <= 100`) and the §8.2 error envelope.
- Local services bind to `127.0.0.1`; secrets stay server-side and job payloads contain IDs only.

## Review focus

- Same-database but cross-project asset references must fail.
- File headers, decompression size, symlinks and path traversal must not bypass upload controls.
- A locked contract cannot change when current masks or settings change.
- Concurrent reservations and duplicate requests cannot overspend or duplicate work.
- Queued exports and pre-experiment jobs need durable state despite gaps in the initial SQL.

## Tasks

### Task 1: Initial migration and local dependencies

**Files:** API DB models/session/repositories, Alembic revisions, service settings/Dockerfiles and `compose.yaml`; `tests/integrations/test_migrations.py`.

**Interfaces:** Repositories use the source table/column names. Publish transaction-bound unit-of-work access shared by API and worker. `/api/v1/health/live` is free/public; readiness checks DB, Redis and artifact access without paid inference.

- [ ] Write temporary-PostgreSQL tests for fresh migration, UUID defaults, evidence foreign keys, unique observation attempts and state constraints.
- [ ] Apply the supplied SQL through Alembic `0001` and map it without silently renaming `findings`, `mutations` or `regression_exports`.
- [ ] Create reviewed additive migration `0002_workflow_support`: durable `background_jobs` with UUID, project, optional experiment/finding references, kind, state, idempotency key, request fingerprint, sanitized request/result JSON, error code, heartbeat and timestamps. Kinds are `SEGMENT`, `TARGET_CHECK`, `EXPORT`, `RETEST`, `CONFIRM`; states are `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `REVIEW_REQUIRED`.
- [ ] Enforce uniqueness of a non-null job idempotency key within project/kind and reject key reuse with a changed request fingerprint. Keep export jobs pending here; create `regression_exports` only once bundle and manifest assets exist, since its initial schema has neither a pending state nor nullable assets.
- [ ] Configure persistent PostgreSQL, Redis and artifact volumes. Construct database settings from explicit components or a fully expanded URL; do not rely on nested `.env` interpolation.
- [ ] Verify `make test-db` and `docker compose config --quiet`; avoid printing resolved secret-bearing Compose configuration.

### Task 2: Atomic artifact storage and seed provenance

**Files:** Artifact storage service, seed routes/repositories and `tests/unit/test_artifacts.py`, `tests/integrations/test_seed_api.py`.

**Interfaces:** `ArtifactStore.put(project_id, data, kind, media_type) -> ArtifactRef`; `ArtifactStore.read_verified(asset_id) -> bytes`. Seed DTOs reference asset UUIDs. Content route is `GET /api/v1/assets/{asset_id}/content` with owner or explicit public-whitelist authorization.

- [ ] Test MIME/header disagreement, truncated files, excessive dimensions, unsafe filenames, checksum tampering and cross-project references before implementing writes.
- [ ] Normalize EXIF orientation and RGB output; strip nonessential metadata from normalized PNGs. Retain original bytes and their canonical evidence SHA separately from normalized pixel/output hashes, with normalization policy recorded.
- [ ] Use server-generated paths from §7.2, temporary file/fsync/atomic rename and DB registration only after file commitment. Reject symlinks and escaped paths. Add an explicit dry-run-capable orphan maintenance command.
- [ ] Implement project creation/read/list and seed upload/read/list. Require rights acknowledgement and a named human label reviewer; keep original and normalized asset references in the provenance metadata.
- [ ] Verify source-byte hash survives normalization and unauthorized content reads fail with 401/403. `make test-unit` and `make test-db` pass.

### Task 3: Owner auth, contracts and stable read APIs

**Files:** API auth/error/dependency modules, contract/target/job routes, audit repositories and API contract tests.

**Interfaces:** Implement technical specification §8 routes applicable to this phase. Add `GET /projects`, `GET /projects/{id}/seeds`, `GET /seeds/{id}`, `GET /seeds/{id}/masks`, `GET /contracts/{id}`, `GET /projects/{id}/targets`, `GET /jobs/{job_id}`, `GET /findings/{id}` and `GET /regressions`, all under `/api/v1` with sanitized DTOs and owner checks. Lists use the shared `Page<T>` cursor contract.

- [ ] Test missing/wrong owner token, cross-project VTC masks, unreviewed masks, duplicate revisions, forbidden generic target URLs and non-whitelisted artifact access.
- [ ] Implement development owner auth, CORS for the configured web origin and sanitized audit records. Project-scoped checks apply in application services even where simple foreign keys do not enforce them.
- [ ] Create immutable VTC revisions with canonical JSON SHA and increasing version. Require mask assets to belong to the seed and have approval. Lock the chosen revision/snapshot atomically when an experiment starts.
- [ ] Store only curated target adapter settings and server-controlled secret references; never let clients select arbitrary environment variable names. External provider checks remain queued for phase 03.
- [ ] Return durable job status through `GET /jobs/{job_id}`. Pending export IDs use the background job UUID; upon completion it also becomes the corresponding regression export UUID, preserving §8's polling/download links.
- [ ] Verify `make test-contract`, `make test-db` and authorization tests; regenerate OpenAPI/types and ensure no secret fields appear.

### Task 4: Reservations and durable operation intent

**Files:** Shared accounting/operation repository, worker budget module and `tests/integrations/test_budget_reservations.py`.

**Interfaces:** `reserve_operation(experiment_id, operation_key, stage, upper_bound_usd, target_calls, candidate_slots) -> OperationReservation`; `settle_operation(operation_key, provider_usage) -> UsageSettlement`; `mark_unknown_charge(operation_key, reason)`. Money uses Decimal/database numeric values, not floating-point arithmetic.

- [ ] Write concurrent reservation tests for dollar/query/candidate caps, operation-key deduplication, runtime expiry and unresolved charges.
- [ ] Lock the experiment row and create operation/usage intent atomically. Derive reserved target calls and pending candidate slots from unfinished operation records, keeping settled/unknown usage from being counted twice.
- [ ] Enforce contract limits as ceilings; request limits can only tighten them. Snapshot runtime, target settings, verifier and pricing revisions with the run.
- [ ] For pre-experiment jobs, use nullable-experiment usage records linked by operation key to the background job. Require an explicit finite owner-authorized job dollar/call/runtime limit before dispatch; serialize against a deployment-level allowance. Never make a segmentation or smoke call an unaccounted exception.
- [ ] Verify `make test-db`: two competing reservations cannot both exceed the remaining cap; unknown charges remain conservatively consumed.
- [ ] Commit verified phase changes with `feat: persist projects evidence and bounded operation intent`.

## Exit criteria and evidence

- [ ] Fresh PostgreSQL migrations pass, including additive workflow support.
- [ ] A verified seed and reviewed-mask contract round-trip with hashes, ownership and immutable history.
- [ ] Auth, upload integrity, idempotency and concurrent budget tests pass without paid calls.
- [ ] Save migration/schema output, test summaries and generated API contracts for phase 03.

## Handoff

Phase 03 consumes durable jobs, artifact APIs, accounting and the owner-controlled
setup endpoints. Provider adapters must use these primitives before network I/O.
