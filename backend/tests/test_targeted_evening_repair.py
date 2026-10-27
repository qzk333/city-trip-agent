import json

import httpx
from test_model_planning_decisions import runtime
from test_review_agent import response
from test_timed_weather_planning import day, stop

from app.candidate_discovery import query_plan
from app.catalog import CATALOG
from app.llm import model_candidates
from app.planner import build_plan
from app.planning_output import parse_planning_output
from app.schemas import Requirements, TravelProfile
from app.tools import TravelTools
from app.validation import validate_itinerary


def test_repair_uses_a_whole_day_schema_without_requerying_catalog(monkeypatch):
    req = Requirements(start_date="2026-10-27", days=3, day_end="23:00", lodging_mode="route_first")
    blocks = [
        {
            "day": n,
            "evening_activities": [{**stop(pid, "19:15", "20:15"), "time_intent": "night"}],
            "evening_explanation": "以具体晚间散步承接晚餐，通行与照明待核实。",
            "daytime_activities": [],
            "breaks": [],
            "weather_note": "天气未知。",
        }
        for n, pid in enumerate(["westlake", "wulin", "hubin"], start=1)
    ]
    # Use a stable fixture catalog without relying on any external provider.
    catalog = {
        f"p{n}": CATALOG["westlake"].model_copy(update={"id": f"p{n}", "name": f"滨水地点{n}"})
        for n in range(1, 4)
    }
    for n, block in enumerate(blocks, start=1):
        block["evening_activities"][0]["place_id"] = f"p{n}"
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"daily_schedule": blocks})}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    out, trace = {}, []
    model_candidates(
        req,
        runtime(),
        trace,
        lambda *_: None,
        catalog=catalog,
        recommendations=out,
        feedback=[{"code": "reviewer_report", "report": response(days=3, missing=True)}],
        previous_schedule=[day([], number=n) for n in range(1, 4)],
        repair_format=True,
    )
    assert len(calls) == 1 and calls[0]["tool_choice"] == "none"
    task = json.loads(calls[0]["messages"][1]["content"])
    assert len(task["repair_objectives"]["days"]) == 3
    assert "activities" in task["daily_schedule_item_schema"]["required"]
    assert "evening_activities" not in task["daily_schedule_item_schema"]["required"]
    assert all(d["activities"][0]["start"] == "19:15" for d in out["daily_schedule"])
    assert not any(t.get("tool") == "search_places" for t in trace)


def test_legacy_period_blocks_preserve_distinct_visits_to_the_same_place():

    req = Requirements(start_date="2026-10-27")
    schedule = [
        {
            "day": 1,
            "evening_activities": [stop("westlake", "19:00", "20:00")],
            "evening_explanation": "晚间散步待核实。",
            "daytime_activities": [stop("westlake", "10:00", "11:00")],
        }
    ]
    parsed = parse_planning_output(json.dumps({"daily_schedule": schedule}), req, CATALOG)
    assert len(parsed["daily_schedule"][0]["activities"]) == 2


def test_minimum_soft_rest_is_relocated_when_it_consumes_actual_transfer_time():
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    tools = TravelTools(runtime())
    route = tools.route
    tools.route = lambda *args, **kwargs: route(*args, **kwargs).model_copy(update={"duration_minutes": 18})
    schedule = [
        day(
            [stop("westlake", "10:00", "11:00"), stop("gushan_museum", "11:28", "12:28")],
            breaks=[{"start": "11:00", "end": "11:15", "kind": "rest", "reason": "可调整短休"}],
        )
    ]
    options = {"model_schedule": schedule}
    plan = validate_itinerary(req, build_plan(req, list(CATALOG), tools, options), options)
    assert plan.days[0].activities[1].start == "11:28"
    assert not any(c.code == "break_travel_conflict" for c in plan.checks)
    assert any(c.code == "break_adjusted" for c in plan.checks)


def test_unspecified_theme_catalog_includes_street_and_culture_channels():
    queries = query_plan(TravelProfile())
    assert ("特色街区", "city") in queries and ("步行街", "city") in queries
    assert ("博物馆", "culture") in queries and len(queries) <= 6


def test_audit_unavailability_does_not_restore_an_empty_evening_over_a_feasible_repair(monkeypatch):
    from test_timed_weather_planning import select_reply

    from app.agent import generate
    from app.schemas import ScheduleReview

    select_reply(
        monkeypatch,
        [
            [day([stop("gushan_museum", "10:00", "11:00")])],
            [day([stop("gushan_museum", "10:00", "11:00"), stop("wulin", "19:00", "20:00")])],
        ],
    )
    reports = iter(
        [
            ScheduleReview(status="changes_requested", **response(missing=True)),
            ScheduleReview(status="unavailable", summary="审核超时，尚未完成体验复核。"),
        ]
    )
    monkeypatch.setattr(
        "app.review_agent.review_schedule",
        lambda *_a, **_k: (next(reports), {"tool": "schedule_reviewer", "status": "test"}),
    )
    req = Requirements(start_date="2026-10-27", day_end="23:00", pace="balanced", lodging_mode="route_first")
    plan = generate(req, runtime(), lambda *_: None)
    assert plan.planning_decision.selected_attempt == 2
    assert plan.days[0].activities[-1].start == "19:00"
    assert plan.planning_decision.quality_review.status == "unavailable"
    assert any(c.code == "quality_review_unavailable" for c in plan.checks)
    assert not any(c.severity == "error" for c in plan.checks)
