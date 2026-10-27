"""Store demand-driven discovery provenance without changing historical place snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("catalogs", sa.Column("discovery", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("catalogs", "discovery")
