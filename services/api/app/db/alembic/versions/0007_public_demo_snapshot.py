"""Hash-bound public demo snapshots with fresh opaque public asset IDs.

Preparation is private and never publishes by itself. A snapshot becomes
public only through an explicit operator publication action; the public
manifest and asset bytes are verified against these retained hashes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007_public_demo_snapshot"
down_revision = "0006_phase07_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_demo_snapshots",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_experiment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("manifest_json", JSONB(), nullable=False),
        sa.Column("manifest_sha256", sa.CHAR(64), nullable=False),
        sa.Column("private_bindings_json", JSONB(), nullable=False),
        sa.Column("prepared_by", sa.Text(), nullable=False),
        sa.Column(
            "prepared_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("published_by", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("publication_intent", sa.Text()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('DRAFT','PUBLISHED','REVOKED','SUPERSEDED')",
            name="public_demo_snapshot_status",
        ),
        sa.CheckConstraint(
            "provenance IN ('REAL_PROVIDER','LOCAL_FIXTURE')",
            name="public_demo_snapshot_provenance",
        ),
        sa.CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="public_demo_snapshot_hash"),
        sa.CheckConstraint(
            "status IN ('DRAFT','REVOKED') OR published_at IS NOT NULL",
            name="public_demo_snapshot_publication_time",
        ),
    )
    # At most one snapshot may be public at a time, even under concurrency.
    op.create_index(
        "public_demo_snapshot_single_published",
        "public_demo_snapshots",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status='PUBLISHED'"),
    )
    op.create_table(
        "public_demo_assets",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public_demo_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("artifact_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("public_finding_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.CheckConstraint("role IN ('ORIGINAL','MUTATED')", name="public_demo_asset_role"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="public_demo_asset_hash"),
        sa.CheckConstraint(
            "media_type IN ('image/png','image/jpeg','image/webp')",
            name="public_demo_asset_media_type",
        ),
        sa.CheckConstraint("width >= 1 AND height >= 1", name="public_demo_asset_size"),
        sa.UniqueConstraint("snapshot_id", "source_asset_id", name="public_demo_asset_once"),
    )
    op.create_index("public_demo_assets_by_snapshot", "public_demo_assets", ["snapshot_id"])


def downgrade() -> None:
    raise RuntimeError("Retained demo snapshot evidence cannot be discarded")
