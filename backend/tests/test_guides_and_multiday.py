from time import monotonic

import httpx
import pytest

from app.agent import generate
from app.catalog import CATALOG
from app.config import Settings
from app.planner import build_plan
from app.research import public_page_text, research_guides
from app.schemas import GuideResearch, GuideSource, Requirements, TransportAdvice
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def test_six_places_are_distributed_over_all_three_days_even_with_short_model_order():
    req = Requirements(start_date="2026-10-24", days=3)
    catalog = dict(list(CATALOG.items())[:6])
    result = build_plan(req, [next(iter(catalog))], TravelTools(Settings(_env_file=None)), {}, catalog)
    assert [len(day.activities) for day in result.days] == [2, 2, 2]
    assert len({activity.place_id for day in result.days for activity in day.activities}) == 6
    assert not [
        check
        for check in validate_itinerary(req, result, catalog=catalog).checks
        if check.severity == "error"
    ]


def test_no_transit_route_does_not_abort_auto_three_day_plan(monkeypatch):
    req = Requirements(start_date="2026-10-24", days=3)
    settings = Settings(tool_provider="amap", amap_api_key="fixture", _env_file=None)
    queried = []

    def get(self, path, params):
        queried.append((path, params))
        if "geocode" in path:
            return {"geocodes": [{"adcode": "330100"}]}
        if "weather" in path:
            return {"forecasts": []}
        if "transit" in path:
            assert params["city"] == req.city
            return {"route": {"transits": []}}
        return {"route": {"paths": [{"duration": "600", "distance": "3000"}]}}

    source = GuideSource(id="guide-1", title="城市交通攻略", url="https://guide.example/travel")
    guides = GuideResearch(
        status="ok",
        sources=[source],
        advice=[
            TransportAdvice(
                place_ids=list(CATALOG),
                mode="transit",
                advice="攻略建议优先了解公交；无班次时考虑打车。",
                source_ids=[source.id],
            )
        ],
    )
    monkeypatch.setattr(TravelTools, "_get", get)
    monkeypatch.setattr("app.agent.research_guides", lambda *_: guides)
    tools = TravelTools(settings)
    tools.guides = guides
    result = validate_itinerary(req, build_plan(req, list(CATALOG), tools, {}))
    assert len(result.days) == 3 and all(day.activities for day in result.days)
    assert all(len(day.legs) == len(day.activities) + 1 for day in result.days)
    assert not [check for check in result.checks if check.severity == "error"]
    assert any("transit" in path for path, _ in queried)
    assert any(leg.mode == "driving" for day in result.days for leg in day.legs)


def test_navigation_failure_returns_explicit_estimate_with_complete_days(monkeypatch):
    req = Requirements(start_date="2026-10-24", days=3)
    settings = Settings(tool_provider="amap", amap_api_key="fixture", _env_file=None)

    def fail(*args):
        raise ToolUnavailable("本次导航不可用")

    monkeypatch.setattr(TravelTools, "_get", fail)
    result = generate(req, settings, lambda *_: None)
    assert all(day.activities for day in result.days)
    assert result.status == "NEEDS_CONFIRMATION"
    assert any(check.code == "route_estimated" for check in result.checks)
    assert all(
        leg.provider == "estimate" and leg.route_status == "estimated"
        for day in result.days
        for leg in day.legs
    )


def test_transit_query_uses_city_name_not_adcode(monkeypatch):
    tools = TravelTools(
        Settings(tool_provider="amap", amap_api_key="fixture", _env_file=None), city="潮州", adcode="445100"
    )
    seen = []

    def get(path, params):
        seen.append(params)
        return {"route": {"transits": [{"duration": "900", "distance": "2000"}]}}

    monkeypatch.setattr(tools, "_get", get)
    tools.route("a", CATALOG["westlake"], "b", CATALOG["coffee"], "transit")
    assert seen[0]["city"] == "潮州"


def test_research_keeps_real_sources_filters_irrelevant_results_and_marks_excerpt_type(monkeypatch):
    monkeypatch.setattr(
        "app.research.search_guides",
        lambda *_: [
            {
                "title": "杭州三天交通攻略",
                "href": "https://guide.example/hangzhou",
                "body": "杭州古城步行与郊外打车建议",
            },
            {"title": "不相关结果", "href": "https://other.example/", "body": "其他城市"},
            {"title": "杭州恶意地址", "href": "https://127.0.0.1/private", "body": "杭州"},
        ],
    )
    monkeypatch.setattr(
        "app.research.public_page_text", lambda *_: "杭州古城适合步行，远郊需另行确认交通。" * 30
    )
    result = research_guides(
        Requirements(start_date="2026-10-24"), Settings(planning_mode="llm", _env_file=None), monotonic() + 30
    )
    assert result.status == "ok" and len(result.sources) == 1
    assert result.sources[0].content_kind == "page_excerpt"
    assert result.sources[0].url == "https://guide.example/hangzhou"


def test_search_failure_does_not_invent_guide_sources(monkeypatch):
    def fail(*args):
        raise RuntimeError("upstream failure")

    monkeypatch.setattr("app.research.search_guides", fail)
    result = research_guides(
        Requirements(start_date="2026-10-24"), Settings(planning_mode="llm", _env_file=None), monotonic() + 30
    )
    assert result.status == "unavailable" and result.sources == []


def test_page_fetch_pins_public_ip_and_refuses_private_redirects(monkeypatch):
    monkeypatch.setattr("app.research.check_public_dns", lambda _: ["93.184.216.34"])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    actual_client = httpx.Client
    monkeypatch.setattr(
        "app.research.httpx.Client",
        lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(ValueError):
        public_page_text("https://guide.example/travel?city=hangzhou")
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "guide.example"
    assert "authorization" not in requests[0].headers


def test_validator_rejects_unretrieved_source_on_return_leg(requirements):
    result = generate(requirements, Settings(_env_file=None), lambda *_: None)
    result.days[0].legs[-1].source_ids = ["invented-source"]
    assert any(
        check.code == "unknown_guide_source" for check in validate_itinerary(requirements, result).checks
    )
