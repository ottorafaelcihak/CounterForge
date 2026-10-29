"""Immutable candidates and totally ordered append-only verifier revisions."""

from alembic import op

revision = "0004_candidate_evidence"
down_revision = "0003_phase03_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE background_jobs DROP CONSTRAINT background_jobs_kind;
    ALTER TABLE background_jobs ADD CONSTRAINT background_jobs_kind CHECK
      (kind IN ('SEGMENT','TARGET_CHECK','EXPORT','RETEST','CONFIRM','SCENE','MUTATION_SAM'));
    ALTER TABLE mutations
      ADD COLUMN idempotency_key text,
      ADD COLUMN request_sha256 char(64) CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
      ADD COLUMN recipe_sha256 char(64) CHECK (recipe_sha256 ~ '^[0-9a-f]{64}$'),
      ADD COLUMN evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(evidence_json)='object'),
      ADD CONSTRAINT candidate_identity UNIQUE(experiment_id,idempotency_key),
      ADD CONSTRAINT candidate_request_pair CHECK ((idempotency_key IS NULL)=(request_sha256 IS NULL));
    ALTER TABLE mutation_validations
      ADD COLUMN revision int,
      ADD COLUMN previous_validation_id uuid REFERENCES mutation_validations(id) ON DELETE RESTRICT,
      ADD COLUMN policy_version text NOT NULL DEFAULT 'legacy-unversioned';
    WITH ordered AS (
      SELECT id,row_number() OVER (PARTITION BY mutation_id ORDER BY created_at,id) AS n,
             lag(id) OVER (PARTITION BY mutation_id ORDER BY created_at,id) AS prev
      FROM mutation_validations
    ) UPDATE mutation_validations v SET revision=o.n,previous_validation_id=o.prev
      FROM ordered o WHERE v.id=o.id;
    ALTER TABLE mutation_validations ALTER COLUMN revision SET NOT NULL;
    ALTER TABLE mutation_validations ADD CONSTRAINT validation_revision_positive CHECK(revision>0),
      ADD CONSTRAINT validation_revision_unique UNIQUE(mutation_id,revision);
    CREATE INDEX validations_revision_latest ON mutation_validations(mutation_id,revision DESC);
    CREATE FUNCTION order_validation_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE prev mutation_validations%ROWTYPE;
    BEGIN
      PERFORM id FROM mutations WHERE id=NEW.mutation_id FOR UPDATE;
      SELECT * INTO prev FROM mutation_validations WHERE mutation_id=NEW.mutation_id
        ORDER BY revision DESC LIMIT 1;
      IF NEW.revision IS NOT NULL AND NEW.revision <> COALESCE(prev.revision,0)+1 THEN
        RAISE EXCEPTION 'Validation revision must be next';
      END IF;
      IF NEW.previous_validation_id IS NOT NULL AND NEW.previous_validation_id IS DISTINCT FROM prev.id THEN
        RAISE EXCEPTION 'Validation predecessor mismatch';
      END IF;
      NEW.revision := COALESCE(prev.revision,0)+1;
      NEW.previous_validation_id := prev.id;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER validation_revision BEFORE INSERT ON mutation_validations
      FOR EACH ROW EXECUTE FUNCTION order_validation_revision();
    CREATE FUNCTION prevent_evidence_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Evidence history is append-only'; END; $$;
    CREATE TRIGGER immutable_validation BEFORE UPDATE OR DELETE ON mutation_validations
      FOR EACH ROW EXECUTE FUNCTION prevent_evidence_rewrite();
    CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION prevent_evidence_rewrite();
    CREATE FUNCTION protect_generated_candidate() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.image_asset_id IS NOT NULL AND
        (to_jsonb(OLD)-'state'-'updated_at') IS DISTINCT FROM (to_jsonb(NEW)-'state'-'updated_at') THEN
        RAISE EXCEPTION 'Generated candidate evidence is immutable';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER immutable_candidate BEFORE UPDATE ON mutations
      FOR EACH ROW EXECUTE FUNCTION protect_generated_candidate();
    CREATE FUNCTION protect_preparation_request() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.kind IN ('SCENE','MUTATION_SAM') AND
        (OLD.request_json,OLD.request_fingerprint,OLD.project_id,OLD.experiment_id,OLD.kind,OLD.idempotency_key)
        IS DISTINCT FROM (NEW.request_json,NEW.request_fingerprint,NEW.project_id,NEW.experiment_id,NEW.kind,NEW.idempotency_key) THEN
        RAISE EXCEPTION 'Preparation intent is immutable';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER immutable_preparation_request BEFORE UPDATE ON background_jobs
      FOR EACH ROW EXECUTE FUNCTION protect_preparation_request();
    CREATE FUNCTION protect_experiment_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (OLD.config_snapshot,OLD.contract_id,OLD.project_id,OLD.target_config_id)
        IS DISTINCT FROM (NEW.config_snapshot,NEW.contract_id,NEW.project_id,NEW.target_config_id) THEN
        RAISE EXCEPTION 'Experiment evidence snapshot is immutable';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER immutable_experiment_snapshot BEFORE UPDATE ON experiments
      FOR EACH ROW EXECUTE FUNCTION protect_experiment_snapshot();
    CREATE TABLE mutation_request_aliases (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      experiment_id uuid NOT NULL REFERENCES experiments(id) ON DELETE RESTRICT,
      mutation_id uuid NOT NULL REFERENCES mutations(id) ON DELETE RESTRICT,
      idempotency_key text NOT NULL,
      request_sha256 char(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
      request_json jsonb NOT NULL CHECK (jsonb_typeof(request_json)='object'),
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT candidate_request_alias UNIQUE(experiment_id,idempotency_key)
    );
    CREATE INDEX request_aliases_by_candidate ON mutation_request_aliases(mutation_id,created_at);
    CREATE TRIGGER immutable_request_alias BEFORE UPDATE OR DELETE ON mutation_request_aliases
      FOR EACH ROW EXECUTE FUNCTION prevent_evidence_rewrite();
    -- One canonical candidate per normalized output inside one experiment. Content
    -- addressed artifacts make image_asset_id the normalized candidate SHA identity,
    -- so a new key/seed cannot mint a second reviewable row for the same pixels.
    -- Deliberately experiment scoped: no cross-experiment global deduplication.
    -- Unsealed legacy rows may contain duplicates; preserve them without letting
    -- them stand in for a newly generated, sealed candidate.
    CREATE UNIQUE INDEX candidate_canonical_image ON mutations(experiment_id,image_asset_id)
      WHERE image_asset_id IS NOT NULL AND recipe_sha256 IS NOT NULL;
    """)


def downgrade() -> None:
    raise RuntimeError("Immutable candidate history requires a reviewed backup restore")
