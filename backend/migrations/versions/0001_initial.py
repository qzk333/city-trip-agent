"""Initial local workbench schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trips",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("requirements", sa.JSON(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("trip_id", sa.String(32), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("itinerary", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("trip_id", "number"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("trip_id", sa.String(32), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("proposal_id", sa.String(32), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "proposals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("trip_id", sa.String(32), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("itinerary", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("applied_version", sa.Integer(), nullable=True),
        sa.Column("dismissed", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "actions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("trip_id", sa.String(32), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("resource", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("trip_id", "key"),
    )
    for table in ("versions", "runs", "proposals", "actions"):
        op.create_index(f"ix_{table}_trip_id", table, ["trip_id"])


def downgrade():
    for table in ("actions", "proposals", "runs", "versions", "trips"):
        op.drop_table(table)
