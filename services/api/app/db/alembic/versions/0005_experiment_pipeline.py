"""Durable bounded experiment scheduling and retained invocation evidence."""

from alembic import op

revision = "0005_experiment_pipeline"
down_revision = "0004_candidate_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE experiments
      ADD COLUMN dispatch_pending boolean NOT NULL DEFAULT false,
      ADD COLUMN runner_token uuid,
      ADD COLUMN lease_expires_at timestamptz,
      ADD COLUMN delivery_no integer NOT NULL DEFAULT 0,
      ADD COLUMN checkpoint_json jsonb NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE model_observations
      ADD COLUMN operation_key text UNIQUE REFERENCES usage_events(operation_key) ON DELETE RESTRICT,
      ADD COLUMN invocation_json jsonb,
      ADD COLUMN confirmation_job_id uuid REFERENCES background_jobs(id) ON DELETE RESTRICT;
    ALTER TABLE mutation_plans ADD COLUMN strategy_revision text,
      ADD COLUMN evidence_json jsonb;
    ALTER TABLE findings ADD COLUMN confirmation_job_id uuid REFERENCES background_jobs(id) ON DELETE RESTRICT,
      ADD COLUMN demo_approved_by text,
      ADD COLUMN demo_approved_at timestamptz,
      ADD COLUMN demo_validation_sha256 char(64);
    CREATE UNIQUE INDEX canonical_confirmation_job ON background_jobs(finding_id)
      WHERE kind='CONFIRM';
    CREATE FUNCTION revoke_finding_validity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      UPDATE findings SET
        status=CASE WHEN NEW.verdict<>'VALID' THEN 'REVIEW_REQUIRED' ELSE status END,
        demo_approved_by=NULL, demo_approved_at=NULL, demo_validation_sha256=NULL,
        updated_at=now()
      WHERE mutation_id=NEW.mutation_id;
      IF FOUND THEN
        INSERT INTO experiment_events(experiment_id,event_type,payload_json)
          SELECT experiment_id,'FINDING_VALIDITY_CHANGED',jsonb_build_object('finding_id',id,'verdict',NEW.verdict,'demo_approved',false)
          FROM findings WHERE mutation_id=NEW.mutation_id;
      END IF;
      RETURN NEW;
    END;
    $$;
    CREATE TRIGGER finding_validity_changed AFTER INSERT ON mutation_validations
      FOR EACH ROW EXECUTE FUNCTION revoke_finding_validity();
    CREATE FUNCTION immutable_observation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.operation_key IS NOT NULL THEN
        RAISE EXCEPTION 'Experiment observations are immutable';
      END IF;
      RETURN NEW;
    END;
    $$;
    CREATE TRIGGER retained_observation BEFORE UPDATE OR DELETE ON model_observations
      FOR EACH ROW EXECUTE FUNCTION immutable_observation();
    """)


def downgrade() -> None:
    raise RuntimeError("Retained experiment evidence cannot be discarded by downgrade")
