"""Install the supplied, immutable SQL baseline byte-for-byte."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).parents[1].joinpath("0001_initial.sql").read_bytes()
    # One unmodified SQL script, not SQLAlchemy-generated replacement DDL.
    op.get_bind().exec_driver_sql(sql.decode("utf-8"))


def downgrade() -> None:
    raise RuntimeError("The evidence baseline is irreversible; restore an explicit database backup")
