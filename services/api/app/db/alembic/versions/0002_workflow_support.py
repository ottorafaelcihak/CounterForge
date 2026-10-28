"""Durable pending jobs; completed exports remain in regression_exports.

Additive only: it also records reservation quantities on run_operations, links
pre-experiment usage to a durable job and adds the shared deployment allowance.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002_workflow_support"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One immutable blob may serve multiple evidence roles (e.g. an all-white
    # seed and mask). Keep distinct asset identities, deduplicated within role.
    op.drop_constraint("artifact_assets_storage_key_key", "artifact_assets", type_="unique")
    op.create_unique_constraint(
        "artifact_assets_storage_role", "artifact_assets", ["storage_key", "kind"]
    )
    op.add_column(
        "seed_cases",
        sa.Column(
            "provenance_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column(
        "visual_contracts",
        sa.Column(
            "evidence_snapshot_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column("experiments", sa.Column("request_fingerprint", sa.CHAR(64)))
    op.add_column(
        "experiments",
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False, server_default="1200"),
    )
    op.create_check_constraint(
        "experiment_runtime", "experiments", "max_runtime_seconds BETWEEN 60 AND 7200"
    )
    op.create_check_constraint(
        "experiment_request_fingerprint",
        "experiments",
        "request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_table(
        "public_asset_whitelist",
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("artifact_assets.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("published_by", sa.Text(), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("""
    CREATE FUNCTION prevent_contract_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (to_jsonb(NEW) - 'locked_at') IS DISTINCT FROM (to_jsonb(OLD) - 'locked_at')
         OR (OLD.locked_at IS NOT NULL AND NEW.locked_at IS DISTINCT FROM OLD.locked_at) THEN
        RAISE EXCEPTION 'Visual contract revisions are immutable';
      END IF;
      RETURN NEW;
    END;
    $$;
    CREATE TRIGGER immutable_contract_revision BEFORE UPDATE ON visual_contracts
      FOR EACH ROW EXECUTE FUNCTION prevent_contract_rewrite();
    """)
    op.create_table(
        "background_jobs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "experiment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "finding_id", UUID(as_uuid=True), sa.ForeignKey("findings.id", ondelete="RESTRICT")
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="QUEUED"),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("request_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("request_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result_json", JSONB()),
        sa.Column("error_code", sa.Text()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind IN ('SEGMENT','TARGET_CHECK','EXPORT','RETEST','CONFIRM')",
            name="background_jobs_kind",
        ),
        sa.CheckConstraint(
            "state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','REVIEW_REQUIRED')",
            name="background_jobs_state",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="background_jobs_fingerprint"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_json) = 'object'", name="background_jobs_request_object"
        ),
        sa.CheckConstraint(
            "result_json IS NULL OR jsonb_typeof(result_json) = 'object'",
            name="background_jobs_result_object",
        ),
        sa.UniqueConstraint(
            "project_id", "kind", "idempotency_key", name="background_jobs_idempotency"
        ),
    )
    op.create_index(
        "background_jobs_by_project_date", "background_jobs", ["project_id", "created_at"]
    )
    op.create_index("background_jobs_runnable", "background_jobs", ["state", "heartbeat_at"])
    # Reservation quantities live with the operation so pending calls/candidates are
    # derived from unfinished rows instead of a second, divergent counter.
    op.add_column(
        "run_operations",
        sa.Column("target_calls", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_operations",
        sa.Column("candidate_slots", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("run_operations", sa.Column("request_fingerprint", sa.CHAR(64)))
    op.create_check_constraint(
        "run_operation_quantities", "run_operations", "target_calls >= 0 AND candidate_slots >= 0"
    )
    op.create_check_constraint(
        "run_operation_request_fingerprint",
        "run_operations",
        "request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    # Pre-experiment usage has no experiment yet; it is accountable to a durable job.
    op.add_column(
        "usage_events",
        sa.Column(
            "background_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("background_jobs.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "usage_events",
        sa.Column("reserved_calls", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("usage_events", sa.Column("request_fingerprint", sa.CHAR(64)))
    op.add_column("usage_events", sa.Column("dispatched_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("usage_reserved_calls", "usage_events", "reserved_calls >= 0")
    op.create_check_constraint(
        "usage_request_fingerprint",
        "usage_events",
        "request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "usage_accountable_subject",
        "usage_events",
        "num_nonnulls(experiment_id, background_job_id) >= 1",
    )
    op.create_index("usage_by_job", "usage_events", ["background_job_id", "created_at"])
    # Single shared allowance for pre-experiment provider work. No row is created
    # here: an unconfigured deployment has no paid allowance at all.
    op.create_table(
        "deployment_allowance",
        sa.Column("singleton", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column("max_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.Column("consumed_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("consumed_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("configured_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("singleton", name="deployment_allowance_singleton"),
        sa.CheckConstraint("max_usd >= 0 AND consumed_usd >= 0", name="deployment_allowance_usd"),
        sa.CheckConstraint(
            "max_calls >= 0 AND consumed_calls >= 0", name="deployment_allowance_calls"
        ),
    )


def downgrade() -> None:
    # Fails safely if multiple roles now share a blob; never erase evidence to
    # make the old, more restrictive constraint fit.
    op.drop_constraint("artifact_assets_storage_role", "artifact_assets", type_="unique")
    op.create_unique_constraint(
        "artifact_assets_storage_key_key", "artifact_assets", ["storage_key"]
    )
    op.drop_table("deployment_allowance")
    op.drop_index("usage_by_job", table_name="usage_events")
    op.drop_constraint("usage_accountable_subject", "usage_events")
    op.drop_constraint("usage_request_fingerprint", "usage_events")
    op.drop_constraint("usage_reserved_calls", "usage_events")
    op.drop_column("usage_events", "dispatched_at")
    op.drop_column("usage_events", "request_fingerprint")
    op.drop_column("usage_events", "reserved_calls")
    op.drop_column("usage_events", "background_job_id")
    op.drop_constraint("run_operation_request_fingerprint", "run_operations")
    op.drop_constraint("run_operation_quantities", "run_operations")
    op.drop_column("run_operations", "request_fingerprint")
    op.drop_column("run_operations", "candidate_slots")
    op.drop_column("run_operations", "target_calls")
    op.drop_table("background_jobs")
    op.execute("DROP TRIGGER immutable_contract_revision ON visual_contracts")
    op.execute("DROP FUNCTION prevent_contract_rewrite()")
    op.drop_table("public_asset_whitelist")
    op.drop_constraint("experiment_request_fingerprint", "experiments")
    op.drop_constraint("experiment_runtime", "experiments")
    op.drop_column("experiments", "max_runtime_seconds")
    op.drop_column("experiments", "request_fingerprint")
    op.drop_column("visual_contracts", "evidence_snapshot_json")
    op.drop_column("seed_cases", "provenance_json")
