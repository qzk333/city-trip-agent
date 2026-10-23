import httpx
import pytest

from app.catalog import CATALOG
from app.config import Settings
from app.tools import ToolUnavailable, TravelTools


def test_weather_does_not_extend_provider_forecast_dates(monkeypatch):
    tools = TravelTools(Settings(tool_provider="amap", amap_api_key="placeholder", _env_file=None))
    monkeypatch.setattr(
        tools,
        "_get",
        lambda *_: {
            "forecasts": [
                {
                    "casts": [
                        {"date": "2026-10-24", "dayweather": "晴", "daytemp": "22", "nighttemp": "15"},
                    ]
                }
            ]
        },
    )
    from datetime import date

    available = tools.weather(date(2026, 10, 24), 2)
    unavailable = tools.weather(date(2030, 1, 1), 1)
    assert available.status == "partial"
    assert len(available.data["days"]) == 1
    assert unavailable.status == "partial"
    assert unavailable.data["days"] == []


def test_real_route_uses_remote_duration_and_retains_estimate_warning(monkeypatch):
    tools = TravelTools(Settings(tool_provider="amap", amap_api_key="placeholder", _env_file=None))
    monkeypatch.setattr(
        tools,
        "_get",
        lambda *_: {
            "route": {
                "paths": [
                    {"distance": "2400", "duration": "1860"},
                ]
            }
        },
    )
    leg = tools.route("westlake", CATALOG["westlake"], "gushan_museum", CATALOG["gushan_museum"], "walking")
    assert leg.duration_minutes == 31
    assert leg.walking_meters == 2400
    assert leg.provider == "amap" and leg.estimated
    assert "未来" in leg.evidence


def test_http_failure_has_no_secret_in_user_error(monkeypatch):
    tools = TravelTools(Settings(tool_provider="amap", amap_api_key="secret-not-for-output", _env_file=None))

    def failing_get(*_, **__):
        raise httpx.ConnectError("failed URL includes secret-not-for-output")

    monkeypatch.setattr("app.tools.httpx.get", failing_get)
    with pytest.raises(ToolUnavailable) as caught:
        tools.route("a", CATALOG["westlake"], "b", CATALOG["gushan_museum"], "walking")
    assert "secret-not-for-output" not in str(caught.value)
    assert not tools.cache


def test_total_run_deadline_is_enforced():
    tools = TravelTools(Settings(_env_file=None))
    tools.deadline = 0
    with pytest.raises(ToolUnavailable, match="时间预算"):
        tools.route("a", CATALOG["westlake"], "b", CATALOG["gushan_museum"], "walking")
