import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import Requirements


@pytest.fixture
def requirements():
    return Requirements(start_date="2026-10-24", must_visit=["westlake", "gushan_museum"])


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}",
        tool_provider="demo",
        planning_mode="demo",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client
