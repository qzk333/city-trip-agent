"""Snapshot each trip's selected-city catalog without storing user credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trips", sa.Column("catalog", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "catalogs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("city_id", sa.Integer(), nullable=False),
        sa.Column("places", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )


def downgrade():
    op.drop_table("catalogs")
    op.drop_column("trips", "catalog")
