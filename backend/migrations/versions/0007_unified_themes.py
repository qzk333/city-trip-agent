"""Collapse old interests into the single profile.themes field, preserving snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for name in ("trips", "versions", "proposals"):
        table = sa.table(name, sa.column("id", sa.String), sa.column("requirements", sa.JSON))
        for row in connection.execute(sa.select(table.c.id, table.c.requirements)).mappings():
            value = row["requirements"]
            if not isinstance(value, dict) or "interests" not in value:
                continue
            value = dict(value)
            old = value.pop("interests")
            profile = dict(value.get("profile") or {})
            if not profile.get("themes"):
                profile["themes"] = list(dict.fromkeys(old))
            value["profile"] = profile
            connection.execute(table.update().where(table.c.id == row["id"]).values(requirements=value))
    table = sa.table("catalogs", sa.column("id", sa.String), sa.column("discovery", sa.JSON))
    for row in connection.execute(sa.select(table.c.id, table.c.discovery)).mappings():
        value = row["discovery"]
        if value.get("fingerprint") and value.get("preference_schema_version") != 2:
            # Old fingerprints include the removed field. Keep all retrieval evidence intact.
            connection.execute(
                table.update()
                .where(table.c.id == row["id"])
                .values(discovery={**value, "fingerprint": None, "preference_schema_version": 2})
            )


def downgrade():
    # Unified themes cannot be losslessly split back into competing preferences.
    pass
