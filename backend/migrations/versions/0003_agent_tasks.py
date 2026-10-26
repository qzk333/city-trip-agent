"""Persist isolated research handoffs independently of itinerary generation."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("started_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40), nullable=True),
    )
    op.create_index("ix_agent_tasks_run_id", "agent_tasks", ["run_id"])


def downgrade():
    op.drop_index("ix_agent_tasks_run_id", table_name="agent_tasks")
    op.drop_table("agent_tasks")
