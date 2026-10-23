from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import ROOT


def test_initial_migration_matches_models_and_is_repeatable(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "backend/alembic.ini"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(url)
    assert {"trips", "versions", "proposals", "runs", "actions"} <= set(inspect(engine).get_table_names())
    engine.dispose()
