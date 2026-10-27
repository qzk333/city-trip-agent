import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import GuideResearch, Requirements


@pytest.fixture(autouse=True)
def offline_schedule_reviewer(monkeypatch):
    from app.schemas import ScheduleReview

    monkeypatch.setattr(
        "app.review_agent.review_schedule",
        lambda *_args, **_kwargs: (
            ScheduleReview(status="unavailable", summary="离线测试未调用审核模型。", failure_code="offline"),
            {"tool": "schedule_reviewer", "status": "offline"},
        ),
    )


@pytest.fixture(autouse=True)
def offline_guides(monkeypatch):
    monkeypatch.setattr("app.agent.research_guides", lambda *_: GuideResearch())
    from app.amap_gateway import _states
    from app.research import _cache

    _states.clear()
    _cache.clear()


@pytest.fixture
def requirements():
    return Requirements(start_date="2026-10-24", must_visit=["westlake", "gushan_museum"])


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}",
        tool_provider="demo",
        planning_mode="demo",
        local_api_config_path=tmp_path / "api-config.json",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client
