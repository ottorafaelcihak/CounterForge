"""Job-isolated repeatable retests and retained refinement lineage."""

from alembic import op

revision = "0006_phase07_support"
down_revision = "0005_experiment_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE background_jobs DROP CONSTRAINT background_jobs_kind;
    ALTER TABLE background_jobs ADD CONSTRAINT background_jobs_kind CHECK
      (kind IN ('SEGMENT','TARGET_CHECK','EXPORT','RETEST','CONFIRM','SCENE','MUTATION_SAM','MINIMIZE'));
    ALTER TABLE background_jobs ADD COLUMN checkpoint_json jsonb NOT NULL DEFAULT '{}'::jsonb,
      ADD CONSTRAINT followup_idempotency CHECK(kind NOT IN ('MINIMIZE','RETEST') OR idempotency_key IS NOT NULL);
    ALTER TABLE model_observations ADD COLUMN retest_job_id uuid REFERENCES background_jobs(id) ON DELETE RESTRICT,
      ADD CONSTRAINT retest_job_required CHECK((phase='RETEST')=(retest_job_id IS NOT NULL)),
      ADD CONSTRAINT retest_mutation_required CHECK(phase<>'RETEST' OR (mutation_id IS NOT NULL AND seed_id IS NULL));
    DROP INDEX obs_mut_attempt_uni;
    CREATE UNIQUE INDEX obs_mut_attempt_uni ON model_observations(experiment_id,mutation_id,phase,attempt_no)
      WHERE mutation_id IS NOT NULL AND retest_job_id IS NULL;
    CREATE UNIQUE INDEX obs_retest_attempt_uni ON model_observations(retest_job_id,mutation_id,attempt_no)
      WHERE phase='RETEST';
    ALTER TABLE mutations ADD COLUMN minimization_job_id uuid REFERENCES background_jobs(id) ON DELETE RESTRICT,
      ADD CONSTRAINT minimization_parent_required CHECK(minimization_job_id IS NULL OR parent_mutation_id IS NOT NULL);
    CREATE UNIQUE INDEX unfinished_minimization_source ON background_jobs(experiment_id)
      WHERE kind='MINIMIZE' AND state IN ('QUEUED','RUNNING','REVIEW_REQUIRED')
        AND finished_at IS NULL;
    CREATE OR REPLACE FUNCTION protect_preparation_request() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.kind IN ('SCENE','MUTATION_SAM','MINIMIZE','RETEST') AND
        (OLD.request_json,OLD.request_fingerprint,OLD.project_id,OLD.experiment_id,OLD.finding_id,OLD.kind,OLD.idempotency_key)
        IS DISTINCT FROM (NEW.request_json,NEW.request_fingerprint,NEW.project_id,NEW.experiment_id,NEW.finding_id,NEW.kind,NEW.idempotency_key) THEN
        RAISE EXCEPTION 'Preparation intent is immutable';
      END IF;
      RETURN NEW;
    END; $$;
    """)


def downgrade() -> None:
    raise RuntimeError("Retained refinement evidence cannot be discarded")
