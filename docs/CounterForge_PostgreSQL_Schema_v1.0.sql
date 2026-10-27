-- CounterForge PostgreSQL 16+ initial schema
-- Proposed migration, not executed on a live database.

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
