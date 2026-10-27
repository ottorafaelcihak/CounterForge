# CounterForge — Technical Specification & Implementation Blueprint

**Version:** 1.0.0 (hackathon MVP)  
**Date:** 27 October 2026  
**Status:** Implementation-ready design; integrations and performance remain untested until deployed  
**Based on:** `CounterForge_PRD_v1.0.md`  
**Competition:** Meta Global AI Developer Hackathon 2026 — Indie Developer / Push the Frontier  
**Submission deadline:** 4 November 2026, 21:00 US Eastern / **5 November 2026, 03:00 Europe/Madrid**  
**MVP boundary:** Single-image object counting; visual-attribute checks only if the object-counting pipeline passes all P0 gates.

> **North star:** Given a labeled image that a target vision model answers correctly, CounterForge explores controlled image perturbations, rejects invalid variants, finds reproducible model errors, and emits an auditable, runnable regression bundle.
>
> **Non-claim:** It neither proves model correctness, nor establishes causal mechanisms inside a model, nor automatically fixes a model. “Verified failure” means that an image preserved the test's externally established ground truth and the tested model produced an incorrect answer under recorded conditions.

---

## 0. Executive engineering decisions

| ADR | Decision | Rationale / trade-off |
|---|---|---|
| ADR-001 | One repository with Next.js frontend + FastAPI API + Python RQ worker | Clear contracts, rapid development, independently testable execution; slightly more services than a monolith |
| ADR-002 | PostgreSQL 16+ for system of record and Redis for RQ | Durable experiment history, explicit constraints and foreign keys; Redis queues are not source of truth |
| ADR-003 | Filesystem object store for MVP, MinIO adapter optional | Avoid needless infrastructure; persistent volume and SHA-256 are mandatory |
| ADR-004 | Meta-hosted inference only in P0 | No special GPU needed; preserves judge-visible Meta integration |
| ADR-005 | Muse Spark as planner, Muse Image as scene generator, SAM 3.1 as perception provider | Each Meta model has an observable responsibility |
| ADR-006 | Independent deterministic validator plus human review, never model self-certification | Synthetic images can change ground truth silently |
| ADR-007 | Only curated target adapters; no arbitrary external inference URLs in MVP | Avoid SSRF, credential leakage, abuse and unclear authorization |
| ADR-008 | One target evaluation at a time per experiment; concurrency limit configurable, default 1 | Expense bounds and more reliable ordering |
| ADR-009 | Immutable contract snapshots and content-addressed artifacts | Repeatable experiments and auditable provenance |
| ADR-010 | PASS / FAIL / UNKNOWN for tests; VALID / INVALID / REVIEW_REQUIRED for mutations | Missing information is not proof of success or model failure |
| ADR-011 | Regression bundles run *assertions of correctness* against target models | Known-broken models cause test failure; if fixed, the test passes |
| ADR-012 | Selected public read-only demo plus authenticated research controls | Judges can inspect without unlimited access to paid APIs |

### Verified external integration facts

As verified from Meta developer documentation on 27 October 2026:

- API base URL: `https://api.meta.ai/v1`; access via `MODEL_API_KEY` on the backend only.
- Muse Spark: `muse-spark-1.3`, via `/v1/responses` or Chat Completions; structured JSON via `response_format` and JSON Schema is documented for Chat Completions.
- Muse Image: `muse-image-1.0`, via `/v1/images/generations`, `/v1/images/edits` or Responses API.
- SAM: `sam-3.1`, via `/v1/responses`, streaming segmentation records. Parse with Meta's `meta_sam_parser` (Python package `meta-sam-parser`); do **not** JSON-decode the raw segmentation text.
- These facts document the *provider API*, not the CounterForge application API defined below. Validate your actual API key, model entitlements and response shapes in smoke tests before integrating.

Primary references are in §25. Store SDK and parser versions in lockfiles; if Meta changes endpoint behavior, update adapters in isolation.

## 1. Scope, definitions and quality bar

### 1.1 P0 user scenario

A developer with an authorized target multimodal model uploads an image containing five distinguishable blue containers; confirms that the answer to “How many blue containers are visible?” is `5`; configures permitted changes such as background clutter or mild brightness; confirms a segmented object mask; runs an experiment; sees proposals from Muse Spark, candidate transformations from deterministic code and Muse Image, mask/invariant checks using SAM; gets a confirmed wrong target response if one exists; reviews evidence; exports a ZIP with images, manifest, hashes and executable Pytest. The test should **fail** when run against a model that still produces the known bad output.

### 1.2 Nonfunctional targets (proposed, not measured)

- **Correctness:** no `INVALID` or `REVIEW_REQUIRED` mutation is counted as a confirmed model failure; no expected-answer change without creating a new contract revision.
- **Reproducibility:** store provider, model ID/version if exposed, prompt, sampler options, adapter revision, exact input asset SHA-256, model outputs, timestamps, parser version and test verdict.
- **Reliability:** no crash or process reboot can cause an experiment to silently skip a charged/unfinished call. Recovery may quarantine ambiguous calls rather than retrying without approval.
- **Performance:** local non-ML API reads target p95 < 500 ms in a small local evaluation; Meta inference latency is logged and reported separately, not guaranteed.
- **Data integrity:** all final artifacts independently SHA-256 verified on retrieval/export. No secrets in exports or public response DTOs.
- **Spend:** enforce estimated-spend reservations and hard caps on target calls, planned candidates and wall-clock runtime; budget accounting is conservative when exact provider cost is unknown.
- **Demonstration:** one end-to-end genuine reproduced failure plus labeled prerecorded fallback; no invented findings or metrics.

### 1.3 Hard MVP exclusions

No arbitrary website/model scanning, hidden credential harvesting, automatic exploitation or evasion toolkits, unbounded synthetic-content generation, video fuzzing, fine-tuning, physical automation, multi-user billing, production SaaS tenancy, or asserted safety certifications.

## 2. System architecture

```mermaid
flowchart TB
    U[Engineer / judge] --> UI[Next.js browser UI]
    UI -->|HTTP JSON / SSE| API[FastAPI]
    API --> DB[(PostgreSQL)]
    API --> OBJ[(Local artifact volume)]
    API --> Q[(Redis / RQ)]
    Q --> W[Bounded experiment worker]
    W --> DB
    W --> OBJ
    W --> PL[Muse Spark adapter]
    W --> SG[SAM 3.1 adapter + official parser]
    W --> IM[Muse Image adapter]
    W --> MUT[Deterministic compositor / transforms]
    W --> VAL[Invariant validator]
    W --> TGT[Allowlisted target model adapter]
    PL --> META[Meta Model API]
    SG --> META
    IM --> META
    TGT -->|if Meta target| META
    W --> REP[Reproduction + export engine]
    REP --> OBJ
```

### 2.1 Responsibility boundaries

| Component | Owns | Must not own |
|---|---|---|
| Web UI | Experiment input, status, evidence inspection, human review | Credentials, direct Meta API calls, final validity decisions |
| FastAPI | Input normalization, auth, persistence, idempotency, authorization, responses | Expensive image generation or long jobs in request lifecycle |
| Worker/orchestrator | State machine, scheduling, budget reservation, provider operations, failure confirmation | Silently changing ground truth, unrestricted tool execution |
| Meta adapters | Provider-specific wire protocol, retries/circuit breaking, usage recording | Product business rules or unverified provider assumptions |
| Invariant verifier | Tests that intended semantics are preserved, records evidence and uncertainty | Claiming semantic validity solely on generated-model output |
| Target adapter | Fixed model/version/prompt execution, strict output parser | Deciding whether mutation itself was valid |
| Exporter | Portable manifest, evidence, Pytest runner and checksum verification | Persisting raw provider keys |

### 2.2 Deployment topology

Docker Compose: `web` (Next.js), `api` (Uvicorn), `worker` (RQ), `postgres`, `redis`; add `reverse-proxy` only for remote deployment. Persist PostgreSQL data and `/data/artifacts` with mounted volumes. Redis append-only persistence is helpful, but durable job intent lives in PostgreSQL and recovery logic must reconcile it. Keep expensive work on a single bounded worker for the MVP.

## 3. Repository layout

```text
counterforge/
  README.md
  LICENSE
  ATTRIBUTION.md
  .env.example
  .gitignore
  compose.yaml
  Makefile
  pyproject.toml                    # Python workspace / tooling
  uv.lock                           # or pinned requirements, choose one
  apps/web/
    app/(dashboard)/projects/...
    app/(dashboard)/experiments/...
    app/(dashboard)/failures/...
    app/demo/...                    # sanitized, read-only
    components/{mask-viewer,diff-viewer,experiment-timeline}/
    lib/{api,types,queries}/
    package.json
    package-lock.json               # or pnpm-lock.yaml, choose one
  services/api/
    app/main.py
    app/routes/{projects,seeds,contracts,targets,experiments,candidates,findings,exports,health}.py
    app/{auth,dependencies,errors,config}.py
    app/db/{models,session,repositories}.py
    migrations/versions/...
  services/worker/
    app/{runner,scheduler,recovery,steps,budget}.py
  packages/contracts/
    counterforge_contracts/{vtc,plans,responses,events,versions}.py
    schemas/*.schema.json
  packages/meta_adapters/
    counterforge_meta/{spark,image,sam,transport,usage}.py
  packages/mutations/
    counterforge_mutations/{deterministic,compositor,registry}.py
  packages/verification/
    counterforge_verification/{invariants,counting,review,comparators}.py
  packages/targets/
    counterforge_targets/{protocol,meta_spark,fixture,registry}.py
  packages/regression/
    counterforge_regression/{manifest,bundle,runner_template}.py
  evaluations/
    seeds/                     # only publish rights-cleared sample media
    manifests/
    baselines/
  tests/{unit,contracts,integrations,e2e,fixtures}/
  scripts/{bootstrap,smoke_meta,smoke_db,run_demo,export_demo}.sh
  docs/{architecture,api,security,evaluation,submission,limitations}.md
```

**Agent boundaries:** one branch per subsystem. `packages/contracts` is owned by one integrator. Other agents import contract versions rather than independently defining them. Freeze initial API/DTO contracts before parallel implementation. No agent should rename tables, routes or types unilaterally.

## 4. End-to-end event sequence

```mermaid
sequenceDiagram
    actor User
    participant UI as Next.js
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Job as Worker
    participant Meta as Meta adapters
    participant Target as Target adapter
    User->>UI: Upload image, expected count and VTC
    UI->>API: Create seed and contract
    API->>DB: Persist immutable versions
    User->>UI: Start experiment
    UI->>API: POST /experiments
    API->>DB: Transaction: snapshot + initial event
    API-->>UI: 202 {experiment_id, status}
    API->>Job: Enqueue experiment id
    Job->>Target: Evaluate original seed x3
    Target-->>Job: Parsed counts + raw responses
    alt seed consistently correct
      Job->>Meta: Spark plans mutations
      Job->>Meta: SAM extracts target masks
      Job->>Meta: Muse Image creates scene variant
      Job->>Job: Deterministic composite + invariant validation
      alt candidate VALID
        Job->>Target: Evaluate candidate
        alt incorrect response
          Job->>Target: Confirm with repeats
          Job->>DB: Save stable/intermittent finding
        end
      else invalid or uncertain
        Job->>DB: Save rejection/review reason
      end
      Job->>DB: Complete within cap
    else baseline invalid
      Job->>DB: Stop: SEED_INVALID_FOR_SEARCH
    end
    UI->>API: GET /experiments/{id}/events
    API-->>UI: SSE state/evidence updates
    User->>UI: Export confirmed finding
    UI->>API: POST /findings/{id}/export
    API->>Job: Queue ZIP creation
    Job->>DB: Store manifest/checksums
    API-->>UI: Download audited bundle
```

## 5. Versioned Visual Test Contract (VTC)

### 5.1 Canonical JSON format (`vtc.schema.json`)

```json
{
  "schema_version": "1.0.0",
  "case_id": "shelf-count-001",
  "task_type": "object_counting",
  "prompt": "How many blue containers are visible? Return only an integer.",
  "expected": {"kind": "integer", "value": 5, "verification": "human", "reviewer": "owner"},
  "protected": {
    "label": "blue container",
    "expected_instances": 5,
    "mask_asset_ids": ["mask-a", "mask-b", "mask-c", "mask-d", "mask-e"],
    "min_visible_fraction": 0.95
  },
  "allowed_mutations": ["background_replacement", "background_clutter", "brightness_shift"],
  "forbidden_mutations": ["object_addition", "object_removal", "protected_region_repaint"],
  "validation_policy": {"uncertain": "REVIEW_REQUIRED", "require_mask_review": true},
  "reproduction": {"attempts": 3, "required_failures": 3},
  "limits": {"max_candidates": 20, "max_target_calls": 60, "max_estimated_usd": 10.0, "max_runtime_seconds": 1200}
}
```

`mask_asset_ids` above are illustrative logical IDs; the API stores real UUIDs and resolves them to SHA-256-addressed objects. A created contract is immutable once used. Every revision gets a new UUID and increasing `version` within its seed.

### 5.2 Pydantic model (normative application-level contract)

```python
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class ExpectedCount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["integer"] = "integer"
    value: int = Field(ge=0, le=1000)
    verification: Literal["human"] = "human"
    reviewer: str = Field(min_length=1, max_length=100)


class ProtectedObjects(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=2, max_length=100)
    expected_instances: int = Field(ge=1, le=16)
    mask_asset_ids: list[str] = Field(min_length=1, max_length=16)
    min_visible_fraction: float = Field(ge=0.8, le=1)


class ValidationPolicy(BaseModel):
    uncertain: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    require_mask_review: bool = True


class ReproductionPolicy(BaseModel):
    attempts: int = Field(default=3, ge=1, le=5)
    required_failures: int = Field(default=3, ge=1, le=5)


class Limits(BaseModel):
    max_candidates: int = Field(default=20, ge=1, le=100)
    max_target_calls: int = Field(default=60, ge=3, le=500)
    max_estimated_usd: float = Field(default=10.0, gt=0, le=150)
    max_runtime_seconds: int = Field(default=1200, ge=60, le=7200)


class VisualTestContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"
    case_id: str = Field(min_length=3, max_length=120)
    task_type: Literal["object_counting"] = "object_counting"
    prompt: str = Field(min_length=10, max_length=300)
    expected: ExpectedCount
    protected: ProtectedObjects
    allowed_mutations: list[
        Literal["background_replacement", "background_clutter", "brightness_shift"]
    ] = Field(min_length=1)
    forbidden_mutations: list[
        Literal["object_addition", "object_removal", "protected_region_repaint"]
    ] = Field(min_length=1)
    validation_policy: ValidationPolicy
    reproduction: ReproductionPolicy
    limits: Limits
```

Add a model-level validator to ensure `required_failures <= attempts` and `protected.expected_instances == expected.value`, and ensure referenced masks belong to the seed and have been reviewed. JSON Schema is generated from these models and checked by frontend and tests. Do not confuse this product JSON Schema with Meta's provider-level structured-output schema.

### 5.3 Mutation plan contract

```json
{
  "schema_version": "1.0.0",
  "hypothesis": "Non-target background clutter may increase counting mistakes",
  "family": "background_clutter",
  "strength": 0.35,
  "prompt": "Make the background a plausible busy warehouse shelf without adding blue containers",
  "target_region": "background_only",
  "requires_muse_image": true,
  "estimated_cost_usd": 0.02
}
```

Server validates `family` against `allowed_mutations`, bounds `strength` in `[0,1]`, requires target region `background_only` for generative edits in the MVP, strips unsupported instructions and rejects overlong/malformed proposals. The model's cost estimate is advisory only; scheduling uses server-maintained pricing/quotas.

## 6. State machines and orchestration semantics

### 6.1 Experiment states

```text
DRAFT -> BASELINE_VALIDATING -> READY -> PLANNING -> MUTATING
      -> VALIDATING -> EVALUATING -> CONFIRMING -> PLANNING/COMPLETED
            any active -> PAUSE_REQUESTED -> PAUSED -> PLANNING
            any active -> CANCEL_REQUESTED -> CANCELLED
            any active -> BUDGET_EXHAUSTED | FAILED
BASELINE_VALIDATING -> SEED_INVALID_FOR_SEARCH (terminal)
```

**State rules:** `PAUSE_REQUESTED` and `CANCEL_REQUESTED` stop *new* provider calls; an in-flight call completes or times out and is accounted for. Transition updates and append-only state events commit atomically. `resume` restores from `PAUSED` only, using remaining counters. `FAILED` is terminal unless a separate recovery operation creates a new run.

### 6.2 Mutation/finding states

```text
PLANNED -> GENERATED -> VALIDATING -> VALID | INVALID | REVIEW_REQUIRED
VALID -> TESTED_CORRECT | SUSPECTED_FAILURE | EVALUATION_UNKNOWN
SUSPECTED_FAILURE -> STABLE | INTERMITTENT | NOT_REPRODUCED
STABLE -> EXPORTED
REVIEW_REQUIRED -> VALID | INVALID (human adjudication, audited)
```

### 6.3 Durable worker requirements

1. Each job message carries **IDs only**, not API keys, large images, or source media.
2. Each experiment has a database advisory/row lock; only one active runner may own it.
3. Job stages checkpoint to PostgreSQL before advancing; save generated image before status `GENERATED`.
4. `idempotency_key` identifies HTTP starts/exports; repeat client requests must not create duplicate experiments.
5. On startup, recovery identifies stalled stages by heartbeat; resumable CPU work is retried, unknown-state provider calls are quarantined for operator review to avoid accidental duplicate billing.
6. Every provider call creates a planned `usage_event`/reservation before network I/O and reconciles actual spend/usage afterward.
7. Limit concurrent model calls and bound queue length; overflow returns `429` or `EXPERIMENT_QUEUE_FULL`.

### 6.4 One bounded scheduler tick (pseudocode)

```python
async def advance_experiment(experiment_id: UUID) -> None:
    with experiment_lock(experiment_id):
        exp = load_experiment(experiment_id)
        if exp.is_terminal or exp.stop_requested:
            return
        stage = next_durable_stage(exp)
        if not budget.can_reserve(stage.estimated_upper_bound):
            transition(exp, "BUDGET_EXHAUSTED")
            return
        op = reserve_and_record_operation(exp, stage)
    try:
        outcome = await execute_stage(stage, op)
    except ProviderOutcomeUnknown as exc:
        quarantine_operation(op, exc)  # do not blindly repeat a billed request
        mark_experiment_paused_for_review(exp.id)
        return
    commit_stage_result_atomically(op, outcome)
    enqueue_next_stage_if_needed(exp.id)
```

No LLM decides state transitions or overrides the test ground truth; it returns typed *proposals* that server code accepts or rejects.

---
## 7. Exact PostgreSQL schema (migration `0001_initial.sql`)

The following is the intended initial schema, not a claim that a database has been deployed. Create it through Alembic in a single migration. PostgreSQL 16+ is preferred; UUIDs use `pgcrypto`. JSON objects are validated by Pydantic before writes. Use `ON DELETE RESTRICT` for evidence dependencies. For a later multi-user product, add tenant-scoped composite keys / row-level security; the hackathon edition is explicitly single-owner.

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(name) BETWEEN 3 AND 160),
  description text NOT NULL DEFAULT '',
  owner_label text NOT NULL DEFAULT 'local-owner',
  settings_json jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(settings_json) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE artifact_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  kind text NOT NULL CHECK (kind IN (
    'SEED_IMAGE','MUTATION_IMAGE','MASK','THUMBNAIL',
    'RAW_RESPONSE','DIFF','MANIFEST','REGRESSION_ZIP','OTHER'
  )),
  storage_key text NOT NULL UNIQUE,
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  byte_size bigint NOT NULL CHECK (byte_size BETWEEN 1 AND 104857600),
  media_type text NOT NULL,
  width int CHECK (width > 0),
  height int CHECK (height > 0),
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assets_by_project ON artifact_assets(project_id, kind);

CREATE TABLE target_configs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  adapter_key text NOT NULL CHECK (adapter_key IN ('META_SPARK', 'LOCAL_FIXTURE')),
  provider text NOT NULL CHECK (provider IN ('META','FIXTURE')),
  model_id text NOT NULL CHECK (length(model_id) BETWEEN 2 AND 120),
  model_revision text NOT NULL DEFAULT 'provider-managed',
  prompt_template text NOT NULL,
  inference_options jsonb NOT NULL DEFAULT '{}'::jsonb,
  secret_ref text,                 -- env/vault reference, NEVER secret value
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE seed_cases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  image_asset_id uuid NOT NULL REFERENCES artifact_assets(id) ON DELETE RESTRICT,
  case_key text NOT NULL,
  question text NOT NULL,
  expected_json jsonb NOT NULL CHECK (jsonb_typeof(expected_json) = 'object'),
  label_provenance text NOT NULL CHECK (label_provenance IN ('HUMAN','LICENSED_DATASET')),
  label_reviewer text NOT NULL,
  label_verified_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, case_key)
);

CREATE TABLE seed_masks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  seed_id uuid NOT NULL REFERENCES seed_cases(id) ON DELETE RESTRICT,
  mask_asset_id uuid NOT NULL REFERENCES artifact_assets(id) ON DELETE RESTRICT,
  sam_model_id text NOT NULL DEFAULT 'sam-3.1',
  object_label text NOT NULL,
  object_ordinal int NOT NULL CHECK (object_ordinal >= 0),
  geometry_json jsonb NOT NULL,
  reviewed boolean NOT NULL DEFAULT false,
  reviewer text,
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (seed_id, object_ordinal)
);

CREATE TABLE visual_contracts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  seed_id uuid NOT NULL REFERENCES seed_cases(id) ON DELETE RESTRICT,
  version int NOT NULL CHECK (version >= 1),
  schema_version text NOT NULL,
  contract_json jsonb NOT NULL CHECK (jsonb_typeof(contract_json) = 'object'),
  contract_sha256 char(64) NOT NULL CHECK (contract_sha256 ~ '^[0-9a-f]{64}$'),
  locked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (seed_id, version)
);

CREATE TABLE experiments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  contract_id uuid NOT NULL REFERENCES visual_contracts(id) ON DELETE RESTRICT,
  target_config_id uuid NOT NULL REFERENCES target_configs(id) ON DELETE RESTRICT,
  state text NOT NULL DEFAULT 'DRAFT' CHECK (state IN (
    'DRAFT','BASELINE_VALIDATING','READY','PLANNING','MUTATING',
    'VALIDATING','EVALUATING','CONFIRMING','PAUSE_REQUESTED','PAUSED',
    'CANCEL_REQUESTED','CANCELLED','COMPLETED','BUDGET_EXHAUSTED',
    'SEED_INVALID_FOR_SEARCH','FAILED'
  )),
  mode text NOT NULL DEFAULT 'ADAPTIVE' CHECK (mode IN ('ADAPTIVE','RANDOM_BASELINE')),
  config_snapshot jsonb NOT NULL CHECK (jsonb_typeof(config_snapshot) = 'object'),
  max_candidates int NOT NULL CHECK (max_candidates BETWEEN 1 AND 100),
  max_target_calls int NOT NULL CHECK (max_target_calls BETWEEN 3 AND 500),
  max_estimated_usd numeric(12,6) NOT NULL CHECK (max_estimated_usd > 0),
  reserved_usd numeric(12,6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
  spent_usd numeric(12,6) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
  calls_consumed int NOT NULL DEFAULT 0 CHECK (calls_consumed >= 0),
  candidates_created int NOT NULL DEFAULT 0 CHECK (candidates_created >= 0),
  stop_reason text,
  heartbeat_at timestamptz,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  idempotency_key text NOT NULL,
  UNIQUE (project_id, idempotency_key)
);
CREATE INDEX experiments_by_project_date ON experiments(project_id, created_at DESC);
CREATE INDEX experiments_runnable ON experiments(state, heartbeat_at);

CREATE TABLE experiment_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id uuid NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
  event_type text NOT NULL,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX experiment_events_stream ON experiment_events(experiment_id, id);

CREATE TABLE mutation_plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
  planner_model text NOT NULL,
  strategy text NOT NULL CHECK (strategy IN ('META_ADAPTIVE','RANDOM')),
  hypothesis text NOT NULL,
  plan_json jsonb NOT NULL,
  planner_response_asset_id uuid REFERENCES artifact_assets(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mutations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
  plan_id uuid REFERENCES mutation_plans(id) ON DELETE RESTRICT,
  parent_mutation_id uuid REFERENCES mutations(id) ON DELETE RESTRICT,
  image_asset_id uuid REFERENCES artifact_assets(id) ON DELETE RESTRICT,
  family text NOT NULL CHECK (family IN (
    'background_replacement','background_clutter','brightness_shift'
  )),
  strength numeric(5,4) NOT NULL CHECK (strength BETWEEN 0 AND 1),
  generator text NOT NULL CHECK (generator IN ('DETERMINISTIC','MUSE_IMAGE','COMPOSITE')),
  recipe_json jsonb NOT NULL,
  state text NOT NULL CHECK (state IN (
    'PLANNED','GENERATED','VALIDATING','VALID','INVALID','REVIEW_REQUIRED',
    'TESTED_CORRECT','SUSPECTED_FAILURE','EVALUATION_UNKNOWN','STABLE',
    'INTERMITTENT','NOT_REPRODUCED','EXPORTED'
  )),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mutations_by_experiment ON mutations(experiment_id, created_at);

CREATE TABLE mutation_validations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mutation_id uuid NOT NULL REFERENCES mutations(id) ON DELETE RESTRICT,
  verifier_version text NOT NULL,
  verdict text NOT NULL CHECK (verdict IN ('VALID','INVALID','REVIEW_REQUIRED')),
  evidence_json jsonb NOT NULL,
  reviewer text,
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX validations_latest ON mutation_validations(mutation_id, created_at DESC);

CREATE TABLE model_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
  target_config_id uuid NOT NULL REFERENCES target_configs(id) ON DELETE RESTRICT,
  seed_id uuid REFERENCES seed_cases(id) ON DELETE RESTRICT,
  mutation_id uuid REFERENCES mutations(id) ON DELETE RESTRICT,
  phase text NOT NULL CHECK (phase IN ('SEED_BASELINE','SCREENING','CONFIRMATION','RETEST')),
  attempt_no int NOT NULL CHECK (attempt_no BETWEEN 1 AND 20),
  request_fingerprint char(64) NOT NULL,
  raw_output_asset_id uuid REFERENCES artifact_assets(id) ON DELETE RESTRICT,
  parsed_json jsonb,
  outcome text NOT NULL CHECK (outcome IN ('CORRECT','INCORRECT','UNKNOWN','PROVIDER_ERROR')),
  latency_ms int CHECK (latency_ms >= 0),
  provider_request_id text,
  model_version_observed text,
  sampling_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((seed_id IS NOT NULL) <> (mutation_id IS NOT NULL))
);
CREATE UNIQUE INDEX obs_seed_attempt_uni ON model_observations(experiment_id, phase, attempt_no)
  WHERE seed_id IS NOT NULL;
CREATE UNIQUE INDEX obs_mut_attempt_uni ON model_observations(experiment_id, mutation_id, phase, attempt_no)
  WHERE mutation_id IS NOT NULL;

CREATE TABLE findings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
  mutation_id uuid NOT NULL UNIQUE REFERENCES mutations(id) ON DELETE RESTRICT,
  status text NOT NULL CHECK (status IN ('SUSPECTED','STABLE','INTERMITTENT','NOT_REPRODUCED','REVIEW_REQUIRED')),
  confirmation_attempts int NOT NULL DEFAULT 0,
  confirmation_failures int NOT NULL DEFAULT 0,
  summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (confirmation_failures <= confirmation_attempts)
);

CREATE TABLE regression_exports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  finding_id uuid NOT NULL REFERENCES findings(id) ON DELETE RESTRICT,
  bundle_asset_id uuid NOT NULL REFERENCES artifact_assets(id) ON DELETE RESTRICT,
  manifest_asset_id uuid NOT NULL REFERENCES artifact_assets(id) ON DELETE RESTRICT,
  runner_version text NOT NULL,
  status text NOT NULL CHECK (status IN ('READY','FAILED','REVOKED')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usage_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid REFERENCES experiments(id) ON DELETE RESTRICT,
  operation_key text NOT NULL UNIQUE,
  provider text NOT NULL,
  model_id text NOT NULL,
  category text NOT NULL CHECK (category IN ('PLANNER','IMAGE','SAM','TARGET','OTHER')),
  status text NOT NULL CHECK (status IN ('RESERVED','SETTLED','UNKNOWN_CHARGE','VOID')),
  estimated_max_usd numeric(12,6) NOT NULL DEFAULT 0 CHECK (estimated_max_usd >= 0),
  actual_usd numeric(12,6) CHECK (actual_usd >= 0),
  tokens_in bigint CHECK (tokens_in >= 0),
  tokens_out bigint CHECK (tokens_out >= 0),
  request_count int NOT NULL DEFAULT 1 CHECK (request_count >= 1),
  latency_ms int CHECK (latency_ms >= 0),
  provider_request_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  settled_at timestamptz
);
CREATE INDEX usage_by_experiment ON usage_events(experiment_id, created_at);

CREATE TABLE run_operations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id uuid NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
  operation_key text NOT NULL UNIQUE,
  stage text NOT NULL,
  status text NOT NULL CHECK (status IN ('RESERVED','RUNNING','DONE','UNKNOWN','FAILED')),
  started_at timestamptz,
  finished_at timestamptz,
  error_code text,
  result_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id uuid REFERENCES projects(id) ON DELETE RESTRICT,
  actor text NOT NULL,
  action text NOT NULL,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  details_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_time ON audit_events(created_at DESC);
```

**Required repository-level invariant:** when referencing a seed/mask/target/asset from an experiment, all linked rows must belong to its project. The initial SQL has entity FKs but does not enforce cross-table project equality; implement a transaction-level service guard and integration test. A production hardening migration should add composite `(id, project_id)` unique constraints and composite FKs or equivalent database triggers so cross-project references are impossible even if the service is bypassed.

### 7.1 Entity relationship diagram

```mermaid
erDiagram
  PROJECTS ||--o{ ARTIFACT_ASSETS : stores
  PROJECTS ||--o{ TARGET_CONFIGS : configures
  PROJECTS ||--o{ SEED_CASES : owns
  SEED_CASES ||--o{ SEED_MASKS : labels
  SEED_CASES ||--o{ VISUAL_CONTRACTS : versions
  VISUAL_CONTRACTS ||--o{ EXPERIMENTS : drives
  TARGET_CONFIGS ||--o{ EXPERIMENTS : tested_by
  EXPERIMENTS ||--o{ EXPERIMENT_EVENTS : emits
  EXPERIMENTS ||--o{ MUTATION_PLANS : plans
  EXPERIMENTS ||--o{ MUTATIONS : creates
  MUTATION_PLANS ||--o{ MUTATIONS : executes
  MUTATIONS ||--o{ MUTATION_VALIDATIONS : checks
  MUTATIONS ||--o{ MODEL_OBSERVATIONS : evaluated_by
  MUTATIONS ||--o| FINDINGS : yields
  FINDINGS ||--o{ REGRESSION_EXPORTS : packages
  EXPERIMENTS ||--o{ USAGE_EVENTS : bills
  EXPERIMENTS ||--o{ RUN_OPERATIONS : tracks
```

### 7.2 Storage and provenance

- Artifact path: `projects/<project_uuid>/sha256/<first2>/<sha256>.<ext>` inside `/data/artifacts`. UUIDs/canonical paths originate on server, not user filenames. Reject symlinks/path escapes.
- Hash original upload bytes and normalized pixel bytes separately; record transform orientation and ICC profile policy. Export original-byte hash as canonical evidence ID.
- File size P0 limit 10 MiB; formats JPEG/PNG/WebP if decoder supports; max 16 megapixels; normalized PNG outputs for deterministic test assets.
- Object writes use temporary file → fsync → atomic rename; DB points only to committed files. Stale orphan uploads are garbage-collected by an explicit maintenance command.
- `ARTIFACT_ASSETS` is append-only while an experiment exists. Retention default 30 days for local demo (configurable); public demo dataset remains rights-cleared and sanitized.
- Never store raw credentials, private target auth tokens, signed remote URLs or full request headers in `audit_events`, `RAW_RESPONSE` or ZIP manifests.

## 8. HTTP API contract (`/api/v1`)

HTTP JSON unless stated. Long-running operations return `202 Accepted` with an ID. All write endpoints require owner authentication and support a stable `Idempotency-Key` header where noted. All route UUIDs are canonical strings; all list routes use cursor pagination with bounded `limit <= 100`.

| Method | Path | Auth | Request | Success | Side effect |
|---|---|---|---|---|---|
| GET | `/health/live` | public | none | `200` | process status |
| GET | `/health/ready` | owner/internal | none | `200/503` | DB + queue readiness, no paid inference |
| POST | `/projects` | owner | `CreateProject` | `201 Project` | create project |
| GET | `/projects/{project_id}` | owner | - | `200 Project` | read |
| GET | `/assets/{asset_id}/content` | owner (public if whitelisted) | - | `200 image/*` | stream verified image/mask/evidence bytes |
| POST | `/projects/{project_id}/seeds` | owner | multipart `file`,`question`,`expected_count`,`license_ack` | `201 Seed` | normalized upload and hash |
| POST | `/seeds/{seed_id}/segment` | owner | `SegmentationRequest` | `202 JobRef` | queued SAM mask generation |
| POST | `/seeds/{seed_id}/masks/{mask_id}/approve` | owner | `ReviewDecision` | `200 Mask` | audit approval |
| POST | `/seeds/{seed_id}/contracts` | owner | `VisualTestContract` | `201 Contract` | creates immutable version |
| POST | `/contracts/validate` | owner | `VisualTestContract` | `200 ValidationReport` | no DB state change |
| POST | `/projects/{project_id}/targets` | owner | `CreateTargetConfig` | `201 Target` | allowlisted adapter only |
| POST | `/targets/{target_id}/check` | owner | - | `202 JobRef` | optional paid handshake, explicitly billed |
| POST | `/experiments` | owner | `StartExperiment` and Idempotency-Key | `202 ExperimentRef` | lock contract snapshot, enqueue |
| GET | `/experiments/{experiment_id}` | owner/public sanitized | - | `200 ExperimentView` | read |
| GET | `/experiments/{experiment_id}/events` | owner | `Last-Event-ID` | `text/event-stream` | reconnectable progress |
| GET | `/experiments/{experiment_id}/mutations` | owner/public sanitized | `cursor,limit,status` | `200 Page<Mutation>` | read |
| POST | `/mutations/{mutation_id}/review` | owner | `ReviewDecision` | `200 MutationView` | human validity verdict; audit |
| POST | `/experiments/{experiment_id}/pause` | owner | - | `202 ExperimentRef` | stop new provider calls |
| POST | `/experiments/{experiment_id}/resume` | owner | - | `202 ExperimentRef` | requeue checkpoint |
| POST | `/experiments/{experiment_id}/cancel` | owner | - | `202 ExperimentRef` | best-effort cancellation |
| GET | `/experiments/{experiment_id}/findings` | owner/public sanitized | filters | `200 Page<Finding>` | read |
| POST | `/findings/{finding_id}/confirm` | owner | `ConfirmRequest` | `202 JobRef` | bounded retest |
| POST | `/findings/{finding_id}/exports` | owner | idempotency key | `202 ExportRef` | create ZIP |
| GET | `/exports/{export_id}` | owner | - | `200 ExportRef` | read state |
| GET | `/exports/{export_id}/download` | owner | - | `200 application/zip` | audited download |
| POST | `/regressions/{export_id}/retest` | owner | `RetestRequest` | `202 JobRef` | rerun bundle against authorized target (P1) |
| GET | `/experiments/{experiment_id}/metrics` | owner/public sanitized | - | `200 Metrics` | read |
| GET | `/public/demo` | public | - | `200 DemoManifest` | sanitized read-only demo |

**Versioning:** Pydantic DTO schemas published through `/openapi.json`; `apiVersion = 1` and internal `schema_version = 1.0.0`. Breaking changes require `/api/v2`. Provider-specific objects never leak into response DTOs.

### 8.1 Selected request and response examples

**Create project:**

```http
POST /api/v1/projects
Content-Type: application/json
Authorization: Bearer <OWNER_TOKEN>

{"name":"Inventory robustness research","description":"Counting five containers"}
```

**Start experiment:**

```http
POST /api/v1/experiments
Content-Type: application/json
Authorization: Bearer <OWNER_TOKEN>
Idempotency-Key: 98b94587-23a8-482a-9421-f9c48279a4ae

{
  "project_id": "<uuid>",
  "contract_id": "<uuid>",
  "target_config_id": "<uuid>",
  "mode": "ADAPTIVE",
  "limits": {"max_candidates":20,"max_target_calls":60,"max_estimated_usd":10.0}
}
```

```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{"experiment_id":"<uuid>","state":"BASELINE_VALIDATING","links":{"self":"/api/v1/experiments/<uuid>","events":"/api/v1/experiments/<uuid>/events"}}
```

**Review a candidate:**

```json
{
  "decision": "INVALID",
  "reason_code": "OBJECT_COUNT_CHANGED",
  "notes": "Generated scene contains a sixth blue container."
}
```

Human approval is never silent: record actor, timestamp, prior verdict, note and evidence hash. A previous automatic `INVALID` may only be overridden with verified ground-truth audit, not a simple checkbox.

**Failure read DTO (sanitized):**

```json
{
  "id": "<uuid>",
  "status": "STABLE",
  "ground_truth": {"type":"integer","value":5,"verified_by":"human"},
  "model": {"adapter":"META_SPARK","model_id":"muse-spark-1.3","revision":"provider-managed"},
  "mutation": {"family":"background_clutter","strength":0.35},
  "validation": {"verdict":"VALID","verifier_version":"0.1.0","evidence_ids":["<uuid>"]},
  "confirmation": {"incorrect":3,"attempts":3,"raw_observation_ids":["<uuid>","<uuid>","<uuid>"]},
  "assets": {"original_image_id":"<uuid>","mutated_image_id":"<uuid>"},
  "qualification": "3/3 incorrect under the recorded settings; no claim of universal reproducibility"
}
```

### 8.2 Error envelope

```json
{
  "error": {
    "code": "BUDGET_EXCEEDED",
    "message": "Experiment limit would be exceeded by the next operation.",
    "correlation_id": "01J...",
    "retryable": false,
    "details": {"remaining_estimated_usd":0.01}
  }
}
```

Common codes: `INVALID_INPUT` (422), `NOT_FOUND` (404), `UNAUTHORIZED` (401), `FORBIDDEN` (403), `INVALID_STATE` (409), `IDEMPOTENCY_CONFLICT` (409), `REVIEW_REQUIRED` (409), `SEED_INVALID_FOR_SEARCH` (422), `QUEUE_FULL` (429), `BUDGET_EXCEEDED` (409), `PROVIDER_RATE_LIMITED` (503), `PROVIDER_FAILURE` (502), `PROVIDER_OUTCOME_UNKNOWN` (503), `ARTIFACT_CHECKSUM_MISMATCH` (500). Never echo secrets, arbitrary image URLs or raw private prompt text in production error bodies.

### 8.3 Server-sent events

`GET /experiments/{id}/events` replays events with `id > Last-Event-ID`; event ID is monotonically increasing `experiment_events.id`. Example:

```text
id: 144
event: candidate_validated
data: {"experiment_id":"<uuid>","mutation_id":"<uuid>","verdict":"VALID"}

```

The server sends keep-alive comments every ~15 seconds; clients re-fetch experiment snapshot after reconnect because events may be pruned in future. Event payloads are sanitized and contain artifact IDs rather than embedded binary data.

---
## 9. Meta AI integration — exact adapter design

### 9.1 Shared provider transport

The adapter layer exposes stable application types and contains all provider-specific parsing. Create an `OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"], timeout=...)` SDK client *inside the server* and reuse HTTP connections. SDK client construction is not proof of access. The project must have entitlement/credit, model availability and successful smoke tests before integration.

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str
    request_id: str | None
    raw_asset_key: str | None
    usage: dict  # normalized, not provider-specific
    latency_ms: int
    warnings: tuple[str, ...]


class ExperimentPlanner(Protocol):
    async def plan(
        self, contract: dict, history: list[dict], remaining_budget: dict
    ) -> list[dict]: ...


class Segmenter(Protocol):
    async def segment(self, image_bytes: bytes, concept: str) -> list[dict]: ...


class SceneGenerator(Protocol):
    async def create_scene(self, source_bytes: bytes, instruction: str) -> bytes: ...


class TargetModel(Protocol):
    async def evaluate(self, image_bytes: bytes, prompt: str, pinned: dict) -> dict: ...
```

Every adapter: (a) validates outbound MIME and maximum size, (b) uses bounded connect/read timeouts, (c) supports `429` exponential backoff with jitter and `Retry-After`, (d) distinguishes safe pre-send retries from ambiguous timeouts, (e) stores sanitized raw outputs and provider request IDs, (f) never logs base64 images/keys, (g) normalizes rate-limit / refused / incomplete outcomes, (h) records pricing-estimate version, not just a guessed dollar value. The Meta provider key is shared for Spark, Image and SAM; use a separate secret scope for any external *authorized* target.

### 9.2 Muse Spark 1.3: typed experiment plans

**Provider model:** `muse-spark-1.3`. Use Meta's documented **Chat Completions** interface for JSON Schema constrained output, which is appropriate for the small `MutationPlan` DTO. The actual JSON Schema must be generated from the Pydantic type; keep plan schemas small and finite.

```python
from openai import OpenAI
import os, json

client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

# Example request shape only. Implement retry, timeout and validation in adapter.
response = client.chat.completions.create(
    model="muse-spark-1.3",
    messages=[
        {
            "role": "system",
            "content": (
                "You propose safe authorized vision-robustness experiments. "
                "Only use allowed_mutations. Never redefine ground truth or test unapproved targets."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "object_counting",
                    "expected": 5,
                    "allowed_mutations": ["background_clutter", "brightness_shift"],
                    "recent_outcomes": [],
                }
            ),
        },
    ],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "mutation_plan",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "family": {
                        "type": "string",
                        "enum": ["background_clutter", "brightness_shift"],
                    },
                    "strength": {"type": "number"},
                    "hypothesis": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["family", "strength", "hypothesis", "prompt"],
            },
        },
    },
)
plan = json.loads(response.choices[0].message.content)
# NEXT: independently validate against VTC + limits; never execute directly.
```

In production the planner receives counts and acceptance/rejection summaries, not unnecessary personal image content. Include seed image context only if the specific planning step needs it, and keep strict token and time budgets. Model reasoning is **suggestive**, never an authority for validity or safety. Store the planner prompt template version and output hash.

**Fallback:** if structured output is refused, incomplete or invalid, use a bounded deterministic mutation plan. Show the fallback in the audit trail. Do not fabricate an LLM-authored plan.

### 9.3 SAM 3.1: protected-object masks

**Provider model:** `sam-3.1`. Meta documents `POST /v1/responses` with an `input_text` noun phrase plus an `input_image`; the result is streamed and encoded in SAM's special-token grammar. Install `meta-sam-parser` and use the cookbook parser/validator, not manual string splitting. The source media is sent as an image data URL or another officially accepted image reference.

```python
from openai import OpenAI
import os

client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

# Transport skeleton. See official SAM API basics for actual parser integration.
with client.responses.stream(
    model="sam-3.1",
    input=[
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "blue container"},
                {"type": "input_image", "image_url": "data:image/png;base64,<encoded_png>"},
            ],
        }
    ],
) as stream:
    for event in stream:
        pass  # feed streamed events to meta_sam_parser per the official cookbook
```

**Implementation contract:** return `SegmentationResult(objects=[{object_id, bbox_xyxy, mask_asset_id, confidence_or_null}], outcome, diagnostics)`. Require terminal completed state; reject incomplete or malformed mask results to `REVIEW_REQUIRED`. Validate decoded masks have the correct image dimensions and binary/nonempty geometry. Reconcile `N` distinct target masks with the human count; a mismatch is a **review blocker**, not permission to change expected `N`. Users can edit masks via an audited UI before approving.

Parser references: <https://dev.meta.ai/docs/cookbook/sam-api-basics> and <https://dev.meta.ai/docs/sam/client-libraries>.

### 9.4 Muse Image 1.0: scene generation and editing

**Provider model:** `muse-image-1.0`. Meta documents `POST /v1/images/edits`; the API takes reference images and an instruction. The OpenAI Python SDK supports multipart `client.images.edit(...)`; raw JSON supports an `images` array. Never assume a provider-generated mask remains faithful to the input.

```python
from openai import OpenAI
import os

client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

with open("reference_scene.png", "rb") as image_file:
    result = client.images.edit(
        model="muse-image-1.0",
        image=image_file,
        prompt="Change only the background to a busier warehouse shelf. No additional blue containers.",
    )
# Provider outputs may be b64 images or signed URLs; adapter handles both safely.
# The generated output is *not* accepted until protected regions and count are checked.
```

**Recommended P0 path:** use Muse Image to create *background textures or contexts*, then composite **unaltered foreground object pixels** from the seed using human-approved SAM masks. Save intermediate background, foreground mask, composite and final image. If the generated background contains extra instances of the *target* object class, reject or regenerate; even a correct foreground composite is not enough if background adds blue containers. Apply modest color/brightness changes deterministically where the ground-truth guarantee is clearer.

**Signed URL controls:** if provider returns a signed image URL, the adapter should download immediately using a restricted allowlist of provider-approved hosts and TLS verification, then store bytes locally. Do not expose provider URLs in public artifacts. Do not fetch arbitrary user-controlled URLs (SSRF).

### 9.5 Model-under-test adapter

MVP target adapters:

1. `META_SPARK`: evaluate a single image and counting prompt with a known/pinned Meta model ID and supported inference options. Keep target prompt distinct from planner and verifier prompts. This is an **authorized live AI model**, but model version may be provider-managed; explicitly report inability to pin binary weights if true.
2. `LOCAL_FIXTURE`: deterministic fake target whose outputs are intentionally wrong for specific image hashes or fixtures. **Unit/integration testing only. Never present fixture failures as real AI failures.**

Define `TargetResult(raw_text, parsed_answer: int|None, status: CORRECT|INCORRECT|UNKNOWN|PROVIDER_ERROR, model_id, version_claim, options, latency_ms, request_id, raw_asset_sha256)`. Parsing accepts a single integer (possibly surrounding whitespace) and optionally strictly controlled JSON (`{"count":5}`); *do not* coerce verbose ambiguous text to a number or let Spark reinterpret target mistakes. Treat refusals, transport errors, empty/ambiguous answers and cutoff outputs as `UNKNOWN`/`PROVIDER_ERROR`, not count errors.

Add a user-authorized non-Meta target later through a registry with explicit provider, credentials scoped to backend, terms-of-service and rate-limit compliance. The hackathon's Meta-powered core must continue to do essential work.

## 10. Image mutation and validity algorithms

### 10.1 Deterministic image transforms

- `brightness_shift`: for each pixel/channel apply a bounded curve with clipping; record operation, intensity, color-space conversion, software version and source SHA. For counting, conservatively choose ranges that retain target visibility; don't assume a brightness transformation is semantically harmless near black/white saturation.
- `background_clutter`: use an approved non-target-object background or generated scene, then place original source foreground on top with antialias feathering in a *narrow* contour ring; ensure no target-class extra objects appear.
- `background_replacement`: strictly alter pixels outside the protected mask plus a narrowly defined blend margin. Measure protected pixel preservation separately from background changes.

All transforms write a JSON recipe and immutable derived image bytes. For random baseline, use a recorded PRNG seed and the same transform ranges/budget available to the adaptive planner.

### 10.2 Validity verifier: multi-layer gating

A candidate is `VALID` only if the evidence package establishes the contract-specific invariant under the *published policy*. Recommended checks:

1. **Media hygiene:** image decodes, dimensions/size within policy, valid color mode, no malicious or truncated payload.
2. **Provenance:** source and output hashes match registered artifacts, allowed mutation family is in VTC, recipe is complete.
3. **Protected region geometry:** source foreground union mask copied with sufficient overlap and stable component boundaries. Thresholds are configurable and calibrated on human-reviewed examples.
4. **Protected pixel integrity:** compute per-pixel/perceptual difference within the protected region and a visible-fraction estimate; reject or review if nontrivial alteration exists.
5. **Instance count / semantic integrity:** compare expected object instances with SAM detection on generated candidate; if mismatch or ambiguous same-class objects in background, require human review. SAM is fallible and alone cannot prove the count.
6. **Visibility/ambiguity:** if shadows, clipping, occlusion or blending make an object uncertain to a human, return `REVIEW_REQUIRED` even when mask overlap is high.
7. **Independent audit:** manually review all findings presented in the three-minute submission and a sampled subset of accepted mutations; preserve signed reviewer decision.

Output example:

```json
{
  "verdict": "REVIEW_REQUIRED",
  "verifier_version": "0.1.0",
  "checks": [
    {"code": "MEDIA_OK", "status": "PASS"},
    {"code": "PROTECTED_PIXEL_DELTA", "status": "PASS", "value": 0.001},
    {"code": "SAM_COUNT_MATCH", "status": "UNKNOWN", "observed": 4, "expected": 5},
    {"code": "HUMAN_VISIBILITY", "status": "REQUIRED"}
  ],
  "confidence_note": "Detection uncertainty prevents automated acceptance",
  "evidence_asset_ids": ["<uuid>", "<uuid>"]
}
```

**Truth table:** `FAIL` on a mandatory invariant ⇒ `INVALID`; else any `UNKNOWN`/unreviewed mandatory check ⇒ `REVIEW_REQUIRED`; only every mandatory check `PASS` or required explicitly human-approved evidence ⇒ `VALID`. Do not average scores to override a mandatory failure.

### 10.3 Known limitations and narrowing

- Ground truth is an **external human-reviewed statement** about visibility, not an inference by the target, Spark or SAM.
- Photorealism and semantic preservation cannot be formally guaranteed; use high-confidence cases and human audit.
- Mask-preserving compositing may introduce visible seams that a model can exploit; measure and disclose and add a seam-only control baseline.
- Excluding occlusion from P0 reduces ambiguity. Occlusion tests may be P1 with a separately reviewed visibility contract.
- If valid generated scenes are scarce, do not relax the invariant verifier to make the demo appear more successful.

## 11. Search strategy, reproduction and minimization

### 11.1 Adaptive search policy (P1; deterministic P0 fallback)

Start with two or three safe mutation families and an exploration batch. For each batch, supply Spark with prior hypotheses, mutation recipe metadata, whether validity passed, and observed outcome. It outputs one or more new typed proposals. Filter proposals, estimate upper-bound cost, then prioritize using a reproducible objective:

\[
\mathrm{priority}(m) = \frac{\widehat{P}(\text{valid}\mid m)\cdot \widehat{P}(\text{failure}\mid m) + \lambda\,\mathrm{explore}(m)}{\max(\epsilon,\widehat{\text{cost}}(m))}
\]

All terms are estimates; sparse data makes probabilities crude. To avoid overfitting on the first few candidates, reserve at least 20% of candidate budget for random exploration. Always save proposed rankings and actual outcomes; report if the adaptive strategy fails to outperform random.

### 11.2 Detecting and confirming a failure

1. A `VALID` candidate is sent to the target once (`SCREENING`). If `CORRECT`, record and move on.
2. If `INCORRECT`, create `SUSPECTED` finding. The incorrect screening call is **not** counted among the three fresh confirmations.
3. Re-run three **fresh calls** with identical input image bytes, prompt, recorded inference settings and adapter version. Record all outputs, even if correct/refused/error.
4. `STABLE` if 3/3 confirmation calls return incorrect, interpretable counts and invariant review remains valid. `INTERMITTENT` if at least one incorrect response but 3/3 not met. `NOT_REPRODUCED` if 0/3 incorrect. If provider error/unknown dominates, keep as `REVIEW_REQUIRED`; do not turn outages into model failures.
5. Save observed provider model revision if exposed, otherwise `provider-managed` with timestamp and disclaimer.
6. A human checks the primary showcased finding, then marks it demo-approved.

**Statistics:** three failures in three attempts indicate repeatability under a tiny sample, not proof of near-100% failure rate. Do not present this as causal proof or universal model brittleness.

### 11.3 Minimal failure search (P1)

For stable `brightness_shift`, binary search strength back toward zero and re-confirm only after candidate validation. For `background_clutter`, remove clutter elements or replace generated detail using recipe-controlled operations, not an impossible exact regeneration of the same stochastic image. A simpler image that stops failing is a *boundary bracket*, not an algorithm failure. Stop after a fixed budget; store both failed and no-longer-failing neighbors to support the narrative.

### 11.4 Random baseline / ablation

Use the same seed dataset, permitted families, strength distribution, target model, image resolution, mutation-validity tests and **target evaluation budget**. Compare:

- `Random + deterministic` baseline
- `Spark plan + deterministic` (planner contribution)
- `Spark + Muse Image + SAM + verifier` full system

Ablate SAM only if the alternative *still independently establishes validity* via manual labels; don't compare against a misleading baseline that counts invalid mutations as bugs. The P0 deliverable must have a random baseline even if the sample is small.

## 12. Regression bundle specification

Bundle is ZIP, content-addressed and portable. Example:

```text
counterforge-regression-<finding_uuid>.zip
  manifest.json
  README.md
  evidence/
    original.png
    failing.png
    protected-mask.png
    comparison.png
    target-responses.json
    validity-report.json
  tests/
    test_visual_regression.py
  runner/
    target_adapter.py               # reads env; no embedded token
  requirements.txt
  sha256sums.txt
```

### 12.1 Manifest (example)

```json
{
  "format_version": "1.0.0",
  "finding_id": "<uuid>",
  "test_id": "shelf-count-001--background-clutter",
  "task": "object_counting",
  "question": "How many blue containers are visible? Return only an integer.",
  "expected": {"kind":"integer","value":5,"verified_by":"human"},
  "model": {
    "adapter":"META_SPARK",
    "model_id":"muse-spark-1.3",
    "revision":"provider-managed",
    "sampling_options": {"temperature":0}
  },
  "invariant_verification": {
    "verdict":"VALID",
    "reviewed_by":"owner",
    "verifier_version":"0.1.0"
  },
  "confirmation": {"screening_failures":1,"fresh_attempts":3,"fresh_failures":3},
  "mutation": {"family":"background_clutter","strength":0.35,"recipe_version":"1.0.0"},
  "files": {
    "original":"evidence/original.png",
    "failing":"evidence/failing.png",
    "validity_report":"evidence/validity-report.json"
  },
  "sha256": {"evidence/failing.png":"<actual SHA-256 hex at export time>"},
  "recorded_at":"2026-10-30T10:00:00Z",
  "limitation":"Re-running a provider-managed model may not reproduce historical behavior"
}
```

**Runner semantics:** verify `sha256sums.txt`, load model config from manifest, require `MODEL_API_KEY` from environment, evaluate image with pinned prompt/settings, strictly parse single-integer output and assert `parsed_count == expected.value`. Thus on a known-broken model the Pytest **runs correctly and fails the assertion**; on a fixed model it passes. A separate `--replay-recorded` mode can deterministically demonstrate the historic failure without network access; label it **recorded replay**, not live reproduction. Do not generate tests that intentionally assert the broken output as success.

**Packaging security:** no code from LLM plan/target raw response is executed or imported; all generated source comes from vetted static templates; ZIP extraction safe paths only; checksum manifest generated after all files; no credentials or internal storage path leaks.

## 13. Frontend specification — page by page

| Page | Route | Essential elements | States/empty/error handling |
|---|---|---|---|
| Dashboard | `/projects` | experiment metrics, projects and creation CTA | no projects; API unavailable |
| New project | `/projects/new` | name, task type, rights/safety acknowledgement | invalid name; unsaved changes |
| Seed setup | `/projects/[id]/seeds/new` | image upload, question, ground-truth count, provenance/license | unsupported media, no label, checksum duplicate |
| Mask review | `/seeds/[id]/masks` | image with SAM masks, object count, contour editor, approve/retry | zero masks, SAM incomplete, manual correction |
| VTC editor | `/seeds/[id]/contracts/new` | allowed families, locked invariants, repeat counts, spending limits | invalid schema, no approved masks |
| Target setup | `/projects/[id]/targets` | allowlisted provider dropdown, model ID, version warning, connection test | missing key, provider quota, not authorized |
| Experiment cockpit | `/experiments/[id]` | timeline, SSE progress, counts, total/reserved spend, pause/cancel | paused, restarted, failed, budget exhausted |
| Candidate gallery | `/experiments/[id]/mutations` | original and generated image cards, verdict filters, reject reasons | invalid/review-only cases, masks unavailable |
| Finding detail | `/findings/[id]` | original/variant diff, mask, independent evidence, raw attempt responses, qualification | intermittent/not reproduced, reviewer not approved |
| Regression library | `/regressions` | export status, SHA verification, download, instructions | no findings, failed build |
| Public demo | `/demo` | sanitized recorded run, architecture summary, results and limitations | no live-paid actions, all replay labeled |

**Interaction rules:** explicit `UNKNOWN` and `REVIEW_REQUIRED` labels; mask overlay toggle; downloadable evidence; no colored animation claiming success without stored result; accessible contrast/keyboard flow; responsive 16:9 desktop screenshot optimized for judges. Use server-generated correlation IDs for user-facing support logs.

### 13.1 Frontend/API type sync

Generate an OpenAPI artifact from FastAPI in CI, then generate TypeScript client types using `openapi-typescript` (or `orval`, choose one). UI agents must not handwrite unofficial enums. API DTOs deliberately omit `secret_ref` and raw internal storage keys. Image URLs are short-lived same-origin authenticated download routes rather than arbitrary external asset URLs.

---
## 14. Security, permissions, privacy and threat model

### 14.1 Security model

For the hackathon MVP, provide **single-owner authenticated mutation controls** and a **public read-only demo gallery**. Use a long random `OWNER_AUTH_TOKEN` only for local development; for an internet-hosted dashboard prefer a real session-based identity provider or reverse-proxy authentication. Never include bearer tokens in browser URL parameters, logs, README screenshots or the demo video. CSRF protections apply if cookies are introduced. CORS must allow only the configured frontend origin.

**Secret roles:** `MODEL_API_KEY` (Meta cloud), `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET` or owner token, optional `PUBLIC_BASE_URL`. Render public env vars with `NEXT_PUBLIC_` only when not secrets. Runtime workers receive secrets via environment or secret store, never job payloads or database rows.

### 14.2 Threat matrix

| Threat | Risk | Implementation control |
|---|---|---|
| Arbitrary target URLs / SSRF | Internal network access, metadata theft | No generic URL target adapter in P0; strict registry and provider-managed URLs |
| Prompt injection from image text or model output | Planner attempts unsupported action | Planner emits JSON data only; schema validation and VTC allowlist override all instructions |
| API-credit exhaustion | Money/cap overrun | Server-side hard cap, provider budget notifications, bounded concurrency, no public write APIs |
| Malicious image decompression | Resource exhaustion, parser issues | Decode in size-limited image sandbox; reject oversized dimensions/file types; normalize |
| Regenerated image changes label | Fake model failure | Independent invariant checks, mask review, human oversight, all artifacts hashed |
| Sensitive image accidentally published | Privacy/licensing harm | Explicit rights acknowledgement, no public raw asset routes, sanitized demo whitelist |
| Hidden auth key in exported tests | Credential leakage | Export templates use environment variable placeholder only; scan ZIP before publish |
| Tampered results or seed | False evidence | SHA-256 evidence chain, append-only events, recorded reviewer and versions |
| Provider outage or retried paid calls | Duplicate spend / inconsistent histories | Unknown-charge quarantine, bounded retry policy, reservation journal |
| Dangerous model-target domain | Policy/TOS violation | Authorized targets only, benign product counting tasks, no prohibited content |

### 14.3 Audit requirements

Record project creation, VTC lock/version, mask edits, target configuration, experiment start/pause/resume/cancel, human verdict overrides, successful/failed exports, and public-demo publication. An audit entry contains actor, time, affected entity, action and sanitized before/after references. Never store the entire secret-bearing request.

## 15. Budgets, accounting and observability

### 15.1 Budget reservation algorithm

Each Meta call costs money or consumes quota; static budget alerts are **not** an execution control. Before dispatching a provider call, use a PostgreSQL transaction (`SELECT ... FOR UPDATE` on experiment) and verify:

- `candidates_created + pending_candidates < max_candidates`
- `calls_consumed + reserved_target_calls + operation_target_calls <= max_target_calls`
- `spent_usd + reserved_usd + upper_bound_operation_cost <= max_estimated_usd`
- current wall-clock elapsed <= `max_runtime_seconds`

Then create `run_operations` + `usage_events(status='RESERVED')` and increment reservations. Upon successful provider response, settle actual usage/pricing and release surplus reservation. If cost is uncertain, keep a conservative upper bound until reconciled. Ambiguous network timeout may be **charged**: do not automatically refund reservation as if free. Treat `UNKNOWN_CHARGE` as consumed unless manually resolved. No scheduler path, including confirmation retries, may bypass this check.

**Price configuration:** central `pricing.toml` or DB settings with documented `source_url`, `checked_at`, model SKU and maximum estimate. API/model rates may change. Compute using response usage when available; otherwise mark amount as `estimated` and display it as such. Keep extra margin for image output bandwidth/generation. For budget reporting separate `actual_provider_usage`, `conservative_estimate`, `unresolved_charges`.

### 15.2 Metrics and logs

- **Application:** `counterforge_experiments_started_total`, `experiments_completed_total`, `mutation_valid_total`, `mutation_invalid_total`, `mutation_review_required_total`, `target_calls_total`, `findings_stable_total`, `export_failures_total`.
- **Cost and latency:** request durations by model and operation type, p50/p95, estimated spend/experiment, daily provider usage, queue wait.
- **Research:** accepted-mutation precision (human audited), confirmed failures per 100 **valid** target calls, stable failure fraction, median discovery calls, search-vs-random lift, seed pass consistency.
- **Tracing:** correlation ID across API → RQ job → Meta/target adapter → DB evidence; never trace full provider prompt/media when user data could be sensitive.
- **Health:** database migrations current, Redis reachable, artifact writable, workers heartbeating. Provider connectivity is a separate explicit smoke test and can incur cost.

## 16. Evaluation methodology and reporting

### 16.1 Dataset preparation

Create at least **12 independently labeled seed images** with source rights records: 8 development and 4 held-out. Use unambiguous, countable objects (e.g. distinct containers on a shelf). Record image dimensions, source permission, object count, reviewer, ambiguity notes and SHA-256. Do not tune prompts on held-out images. A synthetic initial dataset is acceptable if labeled and licenses are clear; do not conflate generated ground truth with verified human ground truth.

### 16.2 Comparison protocol

For each seed, require original baseline pass on target before perturbation search. Fix target model/settings/prompt. Allocate equal **target-model query budget** to `RANDOM_BASELINE` and `ADAPTIVE`; also report the distinct number of proposed candidates, invalid candidates and total Meta API spend, since one method might waste more proposals. Predeclare allowed mutations and strength ranges. Human-audit a stratified sample of accepted and rejected candidates blind to which method produced them, when practical.

### 16.3 Required formulas

- **Validity precision (audited):** `human-confirmed valid accepted samples / all audited accepted samples`.
- **Failure discovery rate:** `stable findings / valid screening calls` (also show per total paid target calls, including confirmation).
- **Confirmation fraction:** `stable findings / suspected findings`, with denominator.
- **Adaptive lift:** `adaptive stable findings per equal target-call budget − random stable findings`, optionally ratio if baseline > 0. Report exact counts.
- **Discovery efficiency:** target calls before first stable failure; censored when none found.
- **Invalid-edit rate:** invalid candidate images / all generated candidate images.
- **Ground-truth integrity:** human-reviewed findings with unambiguous preserved answer / audited findings.

For small samples, report `n` and confidence intervals when meaningful, but do not imply statistically significant results from 12 seeds. Analyze where the generator fails, where SAM masks fail, and how many apparent bugs were rejected after ground-truth review.

### 16.4 Demo evidence rules

Do not present fixture-model failures as live foundation-model weaknesses; disclose sampled tests, previously cached API outputs, any edits to a failure image, and any manual review that made a finding eligible. Archive the exact three-minute demo source media along with experiment IDs used in the submission.

## 17. Test strategy

### 17.1 Test pyramid

| Layer | Tools | Critical behaviors |
|---|---|---|
| Contract | Pydantic + JSON Schema | invalid enums, cross-field constraints, locked VTC, plan allowlist |
| Unit | Pytest, Pillow, NumPy | masks, image differences, byte hashing, strict answer parsing, budget math |
| DB | Pytest + temporary PostgreSQL | foreign keys, project guards, migrations, transactional reservation, state changes |
| Adapter | Recorded provider fixtures + monkeypatches | SAM incomplete output, image URLs/base64, quota errors, retry boundaries |
| Integration | Real Meta API *small gated suite* | Spark structured plan, SAM mask parse, Muse Image output, target counting |
| API | FastAPI TestClient/httpx | 401/403, DTO shape, idempotency, SSE replay, download checksums |
| Worker | RQ + PostgreSQL | crash/resume, duplicate enqueues, pause and budget exhaustion |
| E2E | Playwright | one full happy path, one invalid mutation, export and read-only gallery |
| Evaluation | Dataset harness | adaptive/random fair budget, confirmed failure counts, human audit |
| Security | Secret scanner + adversarial fixtures | forbidden URL, ZIP traversal, disallowed file types, prompt injection |

### 17.2 Acceptance test matrix

| ID | Preconditions | Action | Expected result |
|---|---|---|---|
| AT-01 | Owned PNG with verified `N=5` | Create seed/VTC | Stored normalized asset, source SHA, immutable contract version |
| AT-02 | Wrong baseline count | Start experiment | `SEED_INVALID_FOR_SEARCH` and no paid mutation search |
| AT-03 | API SAM returns 4 when label says 5 | Review seed | Human-review block; no auto ground-truth change |
| AT-04 | Valid background-only generated image | Run verifier | `VALID`, evidence artifact and no target-count change |
| AT-05 | Generated image includes sixth target item | Run verifier | `INVALID` or `REVIEW_REQUIRED`, never confirmed finding |
| AT-06 | Provider times out after request sent | Worker restarts | `UNKNOWN_CHARGE`, no blind duplicate paid call |
| AT-07 | `VALID` image, target reports 4 instead of 5 | Screen and confirm 3x | `STABLE` only for 3/3 fresh incorrect interpretable replies |
| AT-08 | Confirmation gives 4, 5, 4 | Confirm | `INTERMITTENT`; show 2/3, not stable |
| AT-09 | Healthy target returns 5 for exported test | Run Pytest | Test **passes** |
| AT-10 | Broken target returns 4 for exported test | Run Pytest | Test **fails** with expected/actual mismatch (runner itself healthy) |
| AT-11 | Repeated `POST /experiments` same key | Retry | Same experiment ID, no duplicated work |
| AT-12 | Max budget reached | Scheduler proposes next image | No new provider calls, `BUDGET_EXHAUSTED` |
| AT-13 | User clicks Pause during active call | Observe status | Current call accounted, no new calls after completion |
| AT-14 | Exported ZIP | Verify SHA and scan files | All checksums match, no embedded secrets, filenames safe |
| AT-15 | Unauthorized user | Query private seed/export | 401/403 with no sensitive body |
| AT-16 | Public gallery | Open without login | Sanitized metrics and demo; no spendable API operations |
| AT-17 | Two query budget matched strategies | Evaluate benchmark | Counts/dollars/rejections compared transparently |
| AT-18 | Real Meta smoke tests enabled | Invoke planner, generator and SAM | Provider response captured/parsing succeeds; failures reported honestly |
| AT-19 | Submitted demo | Open video/repo as an outsider | Accessible, 3 minutes, clearly shows new hackathon work |
| AT-20 | Any copied code/dataset asset | Scan attribution | Compatible license, included notice, no prohibited images |

### 17.3 Quality gate commands

```bash
# Examples to implement as project Makefile targets, NOT claims of existing commands.
make lint            # Ruff, formatting, ESLint, TS strict
make typecheck       # mypy / pyright and TypeScript
make test-unit       # isolated no-network tests
make test-contract   # schemas, DTO versioning, error envelopes
make test-db         # migration and relational invariants
make test-e2e        # browser smoke in compose environment
make smoke-meta      # paid minimal provider checks, explicitly opt-in
make evaluate        # controlled benchmark; outputs JSON metrics + evidence
make verify-export   # checksums and runner self-test
```

`make test` must not accidentally call billable provider endpoints. Place paid tests behind `RUN_META_LIVE_TESTS=1` and rate/spend cap. Seed benchmark sets are checked into the repository only if redistribution rights permit.

## 18. Infrastructure and developer environment

### 18.1 Environment variables (`.env.example`)

```dotenv
APP_ENV=development
API_BIND=0.0.0.0
API_PORT=8000
WEB_PORT=3000
PUBLIC_BASE_URL=http://localhost:3000
MODEL_API_BASE_URL=https://api.meta.ai/v1
MODEL_API_KEY=                   # SERVER ONLY, never commit actual value
DATABASE_URL=postgresql+psycopg://counterforge:${POSTGRES_PASSWORD}@postgres:5432/counterforge
POSTGRES_PASSWORD=CHANGE_TO_RANDOM_VALUE
REDIS_URL=redis://redis:6379/0
ARTIFACT_ROOT=/data/artifacts
OWNER_AUTH_TOKEN=                # long random local token; prefer sessions for public deployment
MAX_CONCURRENT_PROVIDER_CALLS=1
DEFAULT_MAX_CANDIDATES=20
DEFAULT_MAX_TARGET_CALLS=60
DEFAULT_MAX_ESTIMATED_USD=10.0
PUBLIC_DEMO_MODE=read_only
RUN_META_LIVE_TESTS=0
```

`.env` should be readable only by the service account (e.g., chmod `600`) and excluded from commits, tests, screenshots and exported bundles. Production Postgres credentials should be generated, not copied from examples.

### 18.2 Example Compose service graph

```yaml
# Illustrative topology; build paths and Dockerfiles are created in implementation.
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: counterforge
      POSTGRES_USER: counterforge
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set_me}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U counterforge -d counterforge"]
      interval: 5s
      timeout: 3s
      retries: 15
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes: ["redisdata:/data"]
  api:
    build: {context: ., dockerfile: services/api/Dockerfile}
    env_file: .env
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_started}
    volumes: ["artifacts:/data/artifacts"]
    ports: ["127.0.0.1:8000:8000"]
  worker:
    build: {context: ., dockerfile: services/worker/Dockerfile}
    env_file: .env
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_started}
    volumes: ["artifacts:/data/artifacts"]
  web:
    build: {context: ., dockerfile: apps/web/Dockerfile}
    environment:
      API_INTERNAL_URL: http://api:8000
    depends_on: [api]
    ports: ["127.0.0.1:3000:3000"]
volumes:
  pgdata: {}
  redisdata: {}
  artifacts: {}
```

**Warning:** `.env` interpolation inside another `.env` variable is not universally supported by `env_file` consumers. Implementation MUST construct `DATABASE_URL` from `PGHOST/PGUSER/PGPASSWORD` in application settings or export a fully interpolated database URL; never assume `${POSTGRES_PASSWORD}` is expanded inside `DATABASE_URL` by Docker. The snippets are architecture references, not a fully runnable deployment.

### 18.3 Network and remote-server controls

For the Ubuntu workstation, bind published ports to `127.0.0.1` as above. Use Tailscale/SSH tunneling for personal access; a public demo should go behind TLS, a reverse proxy and strict read-only routes. Do not modify existing RDP, desktop sessions, Claude Code or Codex services. Configure `systemd`/Docker Compose restart policies after basic operation is validated; retain separate persistent logs and volumes. No Kubernetes required.

### 18.4 Setup and smoke-check order

1. Confirm Python 3.12, Node.js 20.17+ or supported LTS, Docker Compose v2, Git and disk space.
2. Generate secrets; claim Meta hackathon credits; set `MODEL_API_KEY` through server-side environment.
3. Build containers; run Alembic migrations; verify Postgres/Redis/artifact volume.
4. Run *free* fixture tests and sample image decoder before live APIs.
5. Opt in to three tiny, billable Meta smoke tests: Muse Spark structured output, SAM segmentation/parsing, Muse Image edit/generation.
6. Add first approved seed and run a *real* target baseline. Confirm actual response format and token/cost usage.
7. Start first constrained end-to-end experiment before building polished UI.
8. Verify download/export as a fresh checkout with only environment variables and licensing-safe sample assets.

## 19. Parallel implementation work packages

| Epic | Agent role / main modules | Dependencies | Acceptance gate |
|---|---|---|---|
| E0 — Contracts and bootstrap | Integrator: repo, lockfiles, Pydantic, schema exports, CI | None | All DTO/schema tests pass |
| E1 — Persistence/API | Backend agent: SQL/Alembic, repositories, FastAPI routes | E0 | Create seed/contract/experiment in DB; idempotent API |
| E2 — Meta integration | Model agent: Spark plan, Image edit, SAM parser | E0 | Paid live smoke fixtures captured and normalized |
| E3 — Mutation and invariants | Vision agent: masks, compositor, verifier | E0 + SAM adapter interface | One VALID and one INVALID case with evidence |
| E4 — Target and findings | Evaluation agent: strict count parser, baseline/confirm, observation records | E0 + E1 | Known bad fixture results in 3/3 STABLE; real model eventually demonstrated |
| E5 — Orchestration/budget | Platform agent: Redis/RQ, states, spend reservation, recovery | E1 + E2 + E3 + E4 interfaces | Crash/restart and cap tests pass |
| E6 — Regression exporter | QA agent: manifests, checksums, Pytest runner | E4 + E1 | Healthy target passes; broken target fails assertion |
| E7 — Web app | Frontend agent: setup, cockpit, evidence | E0 OpenAPI + E1 routes | Playwright happy path and uncertainty states |
| E8 — Benchmarks/demo | Research/release agent: 12 seed labels, baseline, measurements, demo | E3–E7 | Honest metrics, 3-minute video, public repo |

### 19.1 Coordination contracts for coding agents

- One integrator owns the canonical API DTOs, DB migrations and state enum definitions. Other agents submit proposals through PRs and fixtures.
- An agent may run tests and write code in its assigned package but must not modify `.env`, paid quotas or public-host settings without approval.
- Use Git branches `feat/contracts`, `feat/meta-adapters`, `feat/mutations`, `feat/evaluation`, `feat/orchestration`, `feat/web`, `test/evidence`.
- Every PR must state input/output DTO versions, tests run, paid calls made, security/quality assumptions and integration conflicts.
- Avoid conflicting migrations; merge schema changes in a single serialized chain. New AI-generated modules must include real error paths, not only happy-path mocks.

### 19.2 Handoff prompt template

```text
You are implementing CounterForge Technical Specification v1.0.
Read /docs/technical-spec.md and /packages/contracts before editing.
Implement only Epic <ID> on a dedicated branch. Do not redefine DTOs or SQL.
Use Meta Model API through the approved adapter interfaces only.
Do not call paid endpoints in unit tests. Never commit credentials.
Provide: changed files, design decisions, unit/contract tests, measured outcomes,
known limitations, integration notes, and any spec changes requiring owner approval.
A feature is incomplete until its failure modes and acceptance tests pass.
```

## 20. Milestone schedule and gates

| Date | Priority | Deliverable | Stop/go gate |
|---|---|---|---|
| 27 Oct | P0 | Repo, contracts, SQL migration, target fixture, owner auth | Seed + contract round-trip; migrations pass |
| 28 Oct | P0 | Meta smoke tests, SAM masks, human review UI | Real SAM mask output parsed; incomplete handled |
| 29 Oct | P0 | Deterministic masks/compositor, Muse Image adapter | At least one generated **VALID** image and one rejected invalid image |
| 30 Oct | **P0 freeze** | Target baseline, screening/confirmation, first regression export | **Real end-to-end seed → mutation → validated failure → failing Pytest regression** |
| 31 Oct | P1 | Spark adaptive planning under fixed budget | A subsequent plan uses prior outcomes; audit shows model use |
| 1 Nov | P1 | Minimization, export UX, crash/resume and spend caps | Real results survive restart; live export works |
| 2 Nov | P1 | Equal-budget random baseline, 12 labeled seeds, manual audit | Metrics with denominators, valid/invalid counts and cost |
| 3 Nov | P0 | Live cockpit, README, attribution, rehearsed demo | QA gates pass, rights and secrets scanned |
| 4 Nov | **Submission** | Public GitHub, accessible product/video, final evidence report | Submitted before **21:00 US ET / 03:00 Spain next day** |

### 20.1 Scope cuts if behind

1. First remove P2 multi-target support, automated repair, video, fine-tuning and CI integrations.
2. Next remove P1 minimization, complex planner prioritization and rich dashboard charts.
3. Preserve P0: **one actual Meta-powered planning step, SAM segmentation, one Muse Image mutation, independent validity review, target call, repeated failure, export and visible evidence**. If no true failure is found, report that honestly and demonstrate a live search plus fixture-labeled regression pipeline rather than fabricating one.

## 21. Three-minute judge demonstration script

| Time | Screen | What must be genuinely true |
|---|---|---|
| 0:00–0:20 | Seed picture and model correct answer | Previously collected input has human-verified correct count |
| 0:20–0:45 | Contract and protected masks | Show explicit invariants and human approval of masks |
| 0:45–1:20 | Spark experiment proposals and Muse Image/SAM process | Real provider outputs or clearly labeled authenticated replay |
| 1:20–1:45 | Candidate gallery, one rejection | Invalid candidate visibly rejected; no silent label changes |
| 1:45–2:10 | Confirmed target failure | Real recorded wrong answer on VALID image with 3/3 fresh confirmations if claimed |
| 2:10–2:40 | Regression artifact and terminal Pytest | Broken model test FAILS as designed; runner itself executes correctly |
| 2:40–3:00 | Metrics / competitive distinction / limits | Counts and costs from actual evaluation, prior work acknowledged |

Camera recording/streaming hardware is not needed beyond your existing computer for screen capture. Public video must accurately distinguish recorded runs from live model execution.

## 22. Hackathon rules-to-evidence compliance map

The entrant-provided Participant Guide is the controlling event guidance known at the time of this specification; recheck the final rules and any Discord clarifications before submitting.

| Published rule / judging criterion | CounterForge implementation | Submission evidence |
|---|---|---|
| Build for Your World or Push the Frontier | Primary: **Push the Frontier** via automated visual-model reliability research | README problem statement and demo pitch |
| Meaningfully incorporate Meta models | Spark planned experiments; Muse Image produces bounded scene variations; SAM provides masks; adapters logged | Code + provider traces + ablation |
| New work created during event | Original planner/orchestrator/contract/verifier/export UI built Oct 27–Nov 4 | Git commit history + `ATTRIBUTION.md` |
| Public repository | Release rights-cleared source and sample assets only | Repository URL in submission |
| Team max 4 / solo allowed | Project tracks contributors and representative | Submission credits |
| 3-minute demo video | Script above, accessible no-auth link | Video URL, exact duration |
| No unauthorized IP/content | Original or licensed images, compatible dependencies | `ATTRIBUTION.md`, dataset manifests |
| No generic image analyzer / basic AI wrapper | Bounded experiments, invariant rejection, repeated model evaluations, Pytest regression exports | End-to-end replay and code |
| Impact 30% | Model QA before production; measurable failure discovery | Baseline comparison and application examples |
| Meta Tooling 25% | Three complementary core Meta models | Model call logs and ablation plan |
| Technical execution 20% | Versioned VTC, durable jobs, hashes, spend caps, testable contracts | Architecture/schema/testing |
| Demo 25% | Visible surprising model failure plus a runnable test | Working demo and recorded terminal run |

**Bonus-prize positioning:** Best Agentic Use-Case may be relevant if Spark meaningfully plans and revises experiments; Best Build on Meta Open Weight Models requires eligible open-weight models and should not be claimed for cloud-only Muse Spark/SAM use. No guarantee of eligibility or award.

## 23. Explicit assumptions, risks and unresolved decisions

| Topic | Default | Owner action / fallback |
|---|---|---|
| User access to Meta Model API | Approved account with `MODEL_API_KEY` and credits | Confirm credentials and billing before live development |
| Model SKU availability | `muse-spark-1.3`, `muse-image-1.0`, `sam-3.1` | Query accessible models; fail fast, don't claim unavailable model use |
| Target model | Authorized Meta Spark vision adapter | If baseline too robust, choose another **authorized** documented model; don't fabricate errors |
| Task | 5-object counting on rights-cleared images | Keep count unambiguous and visible |
| Budget | $10 estimated max/experiment, deployment guard | Adjust using real documented pricing/usage; enforce hard caps |
| Credentials | Environment or secret store | Never public, never in browser, never exported |
| Human review | Required for primary final finding | Demo cannot state “verified” without audit |
| Hosting | Existing Ubuntu, Docker Compose | Local demo via SSH/Tailscale unless public read-only endpoint is secured |
| Query latency | Unknown until live tests | Prefer short jobs, event feed, cached labeled replay |
| Model output stability | Provider may update model | Store invocation timestamp/model metadata; don't promise binary pinning |
| Novelty | Adjacent VLM fuzzers exist | Claim integrated validity contract-to-regression loop, not “first ever” |

## 24. Definition of done and release sign-off

**Hard launch gate:** `AT-01` through `AT-16` and `AT-18` through `AT-20` pass, or any deviations are openly documented without deceptive substitutions; `AT-17` is strongly recommended for competition strength. A full real-model confirmed failure must have matching ground truth, human-reviewed `VALID` mutation, stored call transcripts, repeated results, and a regression Pytest that reports an expected failed assertion against the model under test. *Finding a specific model failure is not guaranteed; the product may still function as a research tool without one, but it makes the demo less compelling.*

**Release checklist:**

- [ ] Alembic migration and SQL integrity pass in a fresh PostgreSQL instance.
- [ ] Meta Spark, Muse Image and SAM live integration smoke tests pass and evidence IDs are stored.
- [ ] VTC immutable and protected source count human-verified.
- [ ] Both a rejected mutation and an accepted mutation are visible with reasons.
- [ ] Target baseline confirmed to pass before search starts.
- [ ] At least one real suspected failure has fresh confirmations, or the lack thereof is disclosed.
- [ ] Invalid/ambiguous candidates are excluded from confirmed failure metrics.
- [ ] Regression ZIP checksums validate; no keys; runner's semantics are correct.
- [ ] Restart/cancel/budget gates prevent uncontrolled paid calls.
- [ ] Rights, licenses, safety constraints, and public-data audit complete.
- [ ] Tests and benchmark results use actual figures with denominators.
- [ ] Public repo and accessible 3-minute demo accurately label replay/live parts.
- [ ] Submission completed before deadline.

## 25. References and verification record

**Meta official documentation, accessed 27 October 2026:**

1. Model API overview, base URL, supported models: <https://dev.meta.ai/docs/overview>
2. Model IDs/hosting modes: <https://dev.meta.ai/docs/models>
3. Meta API quickstart and SDK compatibility: <https://dev.meta.ai/docs/quickstart>
4. Responses API: <https://dev.meta.ai/docs/api-reference/responses/create-response>
5. Structured output via Chat Completions: <https://dev.meta.ai/docs/structured-output>
6. Muse Image edit requests: <https://dev.meta.ai/docs/api-reference/images/edit-image>
7. Muse Image worked examples: <https://dev.meta.ai/docs/cookbook/image-generation-basics>
8. SAM streaming images and masks: <https://dev.meta.ai/docs/sam/overview>
9. SAM official example: <https://dev.meta.ai/docs/cookbook/sam-api-basics>
10. SAM parser implementations: <https://dev.meta.ai/docs/sam/client-libraries>
11. Meta's cookbook: <https://github.com/meta-models/meta-model-cookbook>
12. Competition: <https://dev.meta.ai/events/global-hackathon>
13. Participant Guide (access may require entrant login): <https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details>

**Companion artifact:** `CounterForge_PRD_v1.0.md` (product goals, user requirements, acceptance criteria and planned demo). This specification operationalizes that PRD; when descriptions differ, resolve the conflict via a versioned decision record before coding. All example SQL, compose definitions, code snippets, targets and schemas in this document are **design proposals** until they are tested in an actual repository and environment.
