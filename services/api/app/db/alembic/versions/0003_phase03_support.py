"""Explicit provider billing, fenced setup jobs and immutable mask history."""

from alembic import op

revision = "0003_phase03_support"
down_revision = "0002_workflow_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE usage_events
      ADD COLUMN cost_kind text CHECK (cost_kind IN ('ACTUAL','ESTIMATED','UNBILLED','UNKNOWN')),
      ADD COLUMN accounted_estimate_usd numeric(12,6) NOT NULL DEFAULT 0 CHECK (accounted_estimate_usd >= 0),
      ADD COLUMN semantic_sha256 char(64) CHECK (semantic_sha256 ~ '^[0-9a-f]{64}$'),
      ADD COLUMN pricing_revision text,
      ADD COLUMN outcome text,
      ADD COLUMN attempt_no int NOT NULL DEFAULT 1 CHECK (attempt_no BETWEEN 1 AND 3),
      ADD COLUMN raw_response_asset_id uuid REFERENCES artifact_assets(id) ON DELETE RESTRICT,
      ADD COLUMN metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata_json)='object');
    ALTER TABLE background_jobs
      ADD COLUMN claim_token uuid,
      ADD COLUMN delivery_no int NOT NULL DEFAULT 0 CHECK (delivery_no >= 0),
      ADD COLUMN lease_expires_at timestamptz;
    ALTER TABLE seed_cases
      ADD COLUMN mask_generation int NOT NULL DEFAULT 0 CHECK (mask_generation >= 0),
      ADD COLUMN segmentation_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(segmentation_json)='object');
    ALTER TABLE seed_masks
      ADD COLUMN revision int NOT NULL DEFAULT 1 CHECK (revision >= 1),
      ADD COLUMN active boolean NOT NULL DEFAULT true,
      ADD COLUMN origin text NOT NULL DEFAULT 'SAM' CHECK (origin IN ('SAM','HUMAN','FIXTURE')),
      ADD COLUMN source_job_id uuid REFERENCES background_jobs(id) ON DELETE RESTRICT,
      ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
      ADD COLUMN revision_created_at timestamptz NOT NULL DEFAULT now(),
      ADD COLUMN revision_notes text NOT NULL DEFAULT '',
      ADD COLUMN revision_actor text NOT NULL DEFAULT 'legacy-import',
      ADD CONSTRAINT mask_review_evidence CHECK (reviewed = (reviewer IS NOT NULL AND reviewed_at IS NOT NULL));
    UPDATE seed_masks SET revision_created_at=created_at;
    CREATE UNIQUE INDEX current_mask_asset_unique ON seed_masks(seed_id,mask_asset_id) WHERE active;
    CREATE TABLE seed_mask_revisions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      mask_id uuid NOT NULL REFERENCES seed_masks(id) ON DELETE RESTRICT,
      seed_id uuid NOT NULL REFERENCES seed_cases(id) ON DELETE RESTRICT,
      revision int NOT NULL CHECK (revision >= 1),
      mask_asset_id uuid NOT NULL REFERENCES artifact_assets(id) ON DELETE RESTRICT,
      mask_sha256 char(64) NOT NULL CHECK (mask_sha256 ~ '^[0-9a-f]{64}$'),
      object_label text NOT NULL,
      object_ordinal int NOT NULL,
      origin text NOT NULL CHECK (origin IN ('SAM','HUMAN','FIXTURE')),
      active boolean NOT NULL,
      geometry_json jsonb NOT NULL CHECK (jsonb_typeof(geometry_json)='object'),
      reviewed boolean NOT NULL,
      reviewer text,
      reviewed_at timestamptz,
      actor text NOT NULL,
      notes text NOT NULL,
      created_at timestamptz NOT NULL,
      retired_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(mask_id,revision)
    );
    CREATE TABLE seed_mask_acknowledgements (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      seed_id uuid NOT NULL REFERENCES seed_cases(id) ON DELETE RESTRICT,
      mask_set_sha256 char(64) NOT NULL CHECK (mask_set_sha256 ~ '^[0-9a-f]{64}$'),
      detected_count int CHECK (detected_count >= 0),
      human_count int NOT NULL CHECK (human_count BETWEEN 1 AND 16),
      corrected_count int NOT NULL CHECK (corrected_count=human_count),
      actor text NOT NULL,
      notes text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX mask_ack_by_seed ON seed_mask_acknowledgements(seed_id,mask_set_sha256);
    CREATE TABLE mask_edit_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      seed_id uuid NOT NULL REFERENCES seed_cases(id) ON DELETE RESTRICT,
      key_sha256 char(64) NOT NULL,
      request_sha256 char(64) NOT NULL,
      scope text NOT NULL,
      response_json jsonb NOT NULL CHECK (jsonb_typeof(response_json)='object'),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(seed_id,key_sha256)
    );
    CREATE FUNCTION prevent_mask_history_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'Mask history is append-only';
    END;
    $$;
    CREATE TRIGGER immutable_mask_history BEFORE UPDATE OR DELETE ON seed_mask_revisions
      FOR EACH ROW EXECUTE FUNCTION prevent_mask_history_rewrite();
    CREATE TRIGGER immutable_mask_ack BEFORE UPDATE OR DELETE ON seed_mask_acknowledgements
      FOR EACH ROW EXECUTE FUNCTION prevent_mask_history_rewrite();
    CREATE TRIGGER immutable_mask_request BEFORE UPDATE OR DELETE ON mask_edit_requests
      FOR EACH ROW EXECUTE FUNCTION prevent_mask_history_rewrite();
    """)


def downgrade() -> None:
    raise RuntimeError("Mask history cannot be discarded by downgrade; restore a reviewed backup")
