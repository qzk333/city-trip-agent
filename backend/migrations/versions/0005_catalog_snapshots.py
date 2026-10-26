"""Keep incrementally discovered places with their preview and historical version."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("versions", "proposals"):
        op.add_column(table, sa.Column("catalog", sa.JSON(), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE {table} SET catalog = "
                f"(SELECT trips.catalog FROM trips WHERE trips.id = {table}.trip_id)"
            )
        )


def downgrade():
    op.drop_column("proposals", "catalog")
    op.drop_column("versions", "catalog")
