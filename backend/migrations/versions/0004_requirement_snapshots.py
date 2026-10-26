"""Version the effective requirements alongside the route for reversible natural-language edits."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("versions", "proposals"):
        op.add_column(table, sa.Column("requirements", sa.JSON(), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE {table} SET requirements = "
                f"(SELECT trips.requirements FROM trips WHERE trips.id = {table}.trip_id)"
            )
        )


def downgrade():
    op.drop_column("proposals", "requirements")
    op.drop_column("versions", "requirements")
