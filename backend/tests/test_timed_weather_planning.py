import json
from copy import deepcopy
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from test_model_planning_decisions import model_reply, runtime

from app.agent import generate
from app.catalog import CATALOG
from app.database import AgentTaskRecord
from app.llm import model_candidates
from app.planner import build_plan
from app.planning_context import planning_inputs, weather_context
from app.schemas import Requirements, ToolResult, TravelProfile
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def forecast(days=None):
    return ToolResult(
        status="partial",
        provider="amap",
        observed_at="2026-10-26T09:00:00+00:00",
        evidence="逐日预报，不支持小时降雨",
        data={
            "days": days
            or [
                {
                    "date": "2026-10-27",
                    "dayweather": "小雨",
                    "nightweather": "多云",
                    "daytemp": "25",
                    "reporttime": "2026-10-26 18:00:00",
                }
            ]
        },
    )


def stop(pid, start, end):
    return {"place_id": pid, "start": start, "end": end, "reason": "按游玩时段及位置安排"}


def day(activities, number=1, breaks=None):
    return {
        "day": number,
        "activities": activities,
        "breaks": breaks or [],
        "weather_note": "白天预报小雨，优先比较室内活动；夜间为多云，仍需确认现场情况。",
    }


def select_reply(monkeypatch, schedules, weather=None):
    calls = []

    def select(req, settings, trace, emit, deadline, visible, guides, output, **kwargs):
        calls.append(deepcopy(kwargs))
        schedule = deepcopy(schedules[min(len(calls) - 1, len(schedules) - 1)])
        if isinstance(schedule, Exception):
            raise schedule
        output["daily_schedule"] = schedule
        output["day_groups"] = [[a["place_id"] for a in d["activities"]] for d in schedule]
        trace.append({"tool": "llm_candidates", "status": "ok"})
        return [a["place_id"] for d in schedule for a in d["activities"]]

    monkeypatch.setattr("app.agent.model_candidates", select)
    if weather:
        monkeypatch.setattr(TravelTools, "weather", lambda *_: weather)
    return calls


def test_weather_and_full_candidate_context_reach_model_before_selection(monkeypatch):
    requests = []
    messages = [
        {
            "tool_calls": [
                {"id": "catalog", "function": {"name": "search_places", "arguments": '{"query":""}'}}
            ]
        },
        {
            "content": json.dumps(
                {
                    "place_ids": ["gushan_museum"],
                    "daily_schedule": [day([stop("gushan_museum", "10:00", "11:00")])],
                }
            )
        },
    ]

    def post(url, **kwargs):
        requests.append(deepcopy(kwargs["json"]))
        return httpx.Response(
            200, json={"choices": [{"message": messages.pop(0)}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    monkeypatch.setattr(TravelTools, "weather", lambda *_: forecast())
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    plan = generate(req, runtime(), lambda *_: None)
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["weather"]["days"][0]["dayweather"] == "小雨"
    assert payload["planning_context"]["candidates"][0]["estimated_duration_minutes"] > 0
    assert payload["requirements"]["start_date"] == "2026-10-27"
    assert payload["weather"]["granularity"] == "day_and_night_not_hourly"
    assert plan.planning_decision.weather_provided and plan.planning_decision.source == "model_schedule"
    assert plan.days[0].activities[0].start == "10:00"


def test_partial_weather_unknown_dates_are_not_filled_and_shelter_is_not_invented():
    req = Requirements(start_date="2026-10-27", days=3)
    context = weather_context(req, forecast())
    assert [d["status"] for d in context["days"]] == ["forecast", "unknown", "unknown"]
    assert context["days"][1] == {"date": "2026-10-28", "status": "unknown"}
    from test_candidate_quality import poi

    museum = poi("museum", "历史博物馆")
    p = planning_inputs(req, {museum.id: museum})["candidates"][0]
    assert p["indoor_outdoor"] == "unknown" and "待核实" in p["shelter_hint"]


@pytest.mark.parametrize(
    "schedule",
    [
        [day([stop("westlake", "25:00", "26:00")])],
        [day([stop("westlake", "11:00", "10:00")])],
        [day([stop("invented", "10:00", "11:00")])],
        [day([stop("westlake", "10:00", "11:00")], number=2)],
    ],
)
def test_invalid_timed_model_output_is_rejected(monkeypatch, schedule):
    model_reply(monkeypatch, {"place_ids": ["westlake"], "daily_schedule": schedule})
    with pytest.raises(ToolUnavailable, match="时间安排无效"):
        model_candidates(
            Requirements(start_date="2026-10-27"), runtime(), [], lambda *_: None, recommendations={}
        )


def test_redundant_groups_do_not_discard_a_valid_concrete_schedule(monkeypatch):
    model_reply(
        monkeypatch,
        {
            "place_ids": ["westlake"],
            "day_groups": [[]],
            "daily_schedule": [day([stop("westlake", "10:00", "11:00")])],
        },
    )
    output = {}
    result = model_candidates(
        Requirements(start_date="2026-10-27"), runtime(), [], lambda *_: None, recommendations=output
    )
    assert result == ["westlake"]
    assert output["day_groups"] == [["westlake"]]


def test_schedule_is_authoritative_when_redundant_place_index_is_omitted(monkeypatch):
    model_reply(monkeypatch, {"daily_schedule": [day([stop("gushan_museum", "10:00", "11:00")])]})
    output = {}
    order = model_candidates(
        Requirements(start_date="2026-10-27"), runtime(), [], lambda *_: None, recommendations=output
    )
    assert order == ["gushan_museum"] and output["day_groups"] == [["gushan_museum"]]


def test_review_unavailability_does_not_reintroduce_a_hardcoded_density_judge(monkeypatch):
    schedule = [
        day(
            [stop("gushan_museum", "10:00", "11:00"), stop("wulin", "19:00", "20:00")],
            breaks=[{"start": "11:30", "end": "18:30", "kind": "free", "reason": "自由安排"}],
        )
    ]
    calls = select_reply(monkeypatch, [schedule])
    plan = generate(
        Requirements(start_date="2026-10-27", day_end="23:00", pace="balanced", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
    )
    assert len(calls) == 1
    assert not any(c.code == "schedule_low_engagement" for c in plan.checks)
    assert any(c.code == "quality_review_unavailable" for c in plan.checks)


def test_equivalent_object_day_groups_are_normalized_without_changing_decisions(monkeypatch):
    schedule = [day([stop("gushan_museum", "10:00", "11:00")])]
    model_reply(
        monkeypatch,
        {
            "place_ids": ["gushan_museum"],
            "day_groups": [{"day": 1, "place_ids": ["gushan_museum"]}],
            "daily_schedule": schedule,
        },
    )
    output = {}
    model_candidates(
        Requirements(start_date="2026-10-27"), runtime(), [], lambda *_: None, recommendations=output
    )
    assert output["day_groups"] == [["gushan_museum"]]
    assert output["daily_schedule"][0]["activities"][0]["start"] == "10:00"


def test_evening_time_is_preserved_and_weather_queried_only_once(monkeypatch):
    weather_calls = []

    def query(*_):
        weather_calls.append(1)
        return forecast()

    monkeypatch.setattr(TravelTools, "weather", query)
    calls = select_reply(
        monkeypatch,
        [
            [
                day(
                    [stop("gushan_museum", "10:00", "11:30"), stop("wulin", "19:00", "20:00")],
                    breaks=[
                        {"start": "12:00", "end": "13:00", "kind": "meal", "reason": "午餐"},
                        {"start": "20:15", "end": "21:00", "kind": "meal", "reason": "晚餐"},
                        {
                            "start": "14:00",
                            "end": "17:00",
                            "kind": "free",
                            "reason": "同区自主探索，不指定场所",
                        },
                    ],
                )
            ]
        ],
    )
    req = Requirements(
        start_date="2026-10-27",
        day_end="23:00",
        lodging_mode="route_first",
        profile=TravelProfile(themes=["night"]),
    )
    plan = generate(req, runtime(), lambda *_: None)
    assert plan.days[0].activities[-1].start == "19:00"
    assert plan.days[0].breaks[0].kind == "meal" and plan.days[0].weather_note
    assert len(weather_calls) == 1 and len(calls) == 1
    assert calls[0]["weather"].data["days"][0]["dayweather"] == "小雨"
    assert not [c for c in plan.checks if c.severity == "error"]


def test_model_schedule_can_have_more_than_four_activities(monkeypatch):
    ids = list(CATALOG)[:5]
    catalog = {pid: CATALOG[pid].model_copy(update={"opening": None, "closing": None}) for pid in ids}
    schedule = [day([stop(pid, f"{9 + i * 2:02d}:00", f"{10 + i * 2:02d}:00") for i, pid in enumerate(ids)])]
    select_reply(monkeypatch, [schedule])
    plan = generate(
        Requirements(start_date="2026-10-27", day_end="23:00", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
        catalog=catalog,
    )
    assert len(plan.days[0].activities) == 5


def test_underfilled_timetable_feedback_returns_to_model_once(monkeypatch):
    from test_review_agent import fake_reviews, response

    fake_reviews(monkeypatch, [response(missing=True), response()])
    first = [day([stop("westlake", "09:00", "10:15")])]
    second = [
        day(
            [stop("gushan_museum", "10:00", "11:30"), stop("wulin", "19:00", "20:00")],
            breaks=[
                {"start": "12:00", "end": "13:00", "kind": "meal", "reason": "午餐"},
                {"start": "20:15", "end": "21:00", "kind": "meal", "reason": "晚餐"},
                {"start": "14:00", "end": "17:00", "kind": "free", "reason": "自由逛街及休息"},
            ],
        )
    ]
    calls = select_reply(monkeypatch, [first, second])
    req = Requirements(
        start_date="2026-10-27",
        pace="balanced",
        day_end="23:00",
        lodging_mode="route_first",
        profile=TravelProfile(themes=["night"]),
    )
    plan = generate(req, runtime(), lambda *_: None)
    assert len(calls) == 2 and calls[1]["previous_schedule"]
    assert {c["code"] for c in calls[1]["feedback"]} >= {"reviewer_evening"}
    assert plan.planning_decision.review_rounds == 1
    assert plan.days[0].activities[-1].start == "19:00"
    assert not any(c.code in {"reviewer_evening"} for c in plan.checks)


def test_failed_review_retains_original_preview_and_issues(monkeypatch):
    from test_review_agent import fake_reviews, response

    fake_reviews(monkeypatch, [response(missing=True)])
    calls = select_reply(
        monkeypatch, [[day([stop("westlake", "09:00", "10:15")])], ToolUnavailable("timeout")]
    )
    plan = generate(
        Requirements(start_date="2026-10-27", day_end="23:00", pace="balanced", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
    )
    assert len(calls) == 2 and plan.days[0].activities[0].start == "09:00"
    assert {c.code for c in plan.checks} >= {"reviewer_evening", "schedule_review_unavailable"}


def test_unreachable_model_time_is_not_silently_shifted_and_cannot_save(client, monkeypatch):
    select_reply(monkeypatch, [[day([stop("gushan_museum", "09:00", "10:00")])]])
    monkeypatch.setattr("app.main.request_settings", lambda *_: runtime())
    trip = client.post(
        "/api/trips",
        json={
            "start_date": "2026-10-27",
            "must_visit": ["gushan_museum"],
            "appointments": [{"place_id": "gushan_museum", "day": 1, "start": "09:00", "end": "10:00"}],
        },
    ).json()
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    proposal = run["proposal"]
    assert proposal["itinerary"]["days"][0]["activities"][0]["start"] == "09:00"
    assert any(c["code"] == "late_arrival" for c in proposal["itinerary"]["checks"])
    response = client.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/apply",
        json={"base_version": 0, "idempotency_key": "timed-invalid"},
    )
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        task = db.scalar(
            select(AgentTaskRecord).where(AgentTaskRecord.run_id == run_id, AgentTaskRecord.role == "main")
        )
        assert "weather" in task.input_snapshot and len(task.result["schedule_attempts"]) == 2


@pytest.mark.parametrize(
    "block,code",
    [
        ({"start": "09:30", "end": "10:30", "kind": "meal", "reason": "重叠"}, "break_overlap"),
        ({"start": "08:00", "end": "08:30", "kind": "rest", "reason": "超界"}, "break_window"),
        ({"start": "11:00", "end": "11:50", "kind": "meal", "reason": "没留交通"}, "break_travel_conflict"),
    ],
)
def test_breaks_are_checked_against_activity_windows_and_route_time(block, code):
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    tools = TravelTools(runtime())
    route = tools.route
    tools.route = lambda *args, **kwargs: route(*args, **kwargs).model_copy(update={"duration_minutes": 50})
    options = {
        "model_schedule": [
            day([stop("westlake", "10:00", "11:00"), stop("gushan_museum", "12:00", "13:00")], breaks=[block])
        ]
    }
    plan = validate_itinerary(req, build_plan(req, list(CATALOG), tools, options), options)
    if code in {"break_overlap", "break_travel_conflict"}:
        assert not any(c.code == code for c in plan.checks)
        assert any(c.code == "break_adjusted" for c in plan.checks)
        assert any(b.kind == "meal" for b in plan.days[0].breaks)
    else:
        assert any(c.code == code for c in plan.checks)


def test_soft_meal_is_trimmed_for_actual_travel_without_moving_activity():
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    tools = TravelTools(runtime())
    schedule = [
        day(
            [stop("westlake", "10:00", "11:00"), stop("gushan_museum", "12:00", "13:00")],
            breaks=[{"start": "11:00", "end": "11:50", "kind": "meal", "reason": "午餐"}],
        )
    ]
    options = {"model_schedule": schedule}
    plan = validate_itinerary(req, build_plan(req, list(CATALOG), tools, options), options)
    assert plan.days[0].activities[1].start == "12:00"
    assert plan.days[0].breaks[0].end == "11:40"
    assert any(c.code == "break_adjusted" for c in plan.checks)
    assert not any(c.severity == "error" for c in plan.checks)


def test_exact_physical_transfer_is_not_shifted_for_an_extra_fixed_margin():
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    tools = TravelTools(runtime())
    options = {
        "model_schedule": [day([stop("westlake", "10:00", "11:00"), stop("gushan_museum", "11:20", "12:20")])]
    }
    plan = validate_itinerary(req, build_plan(req, list(CATALOG), tools, options), options)
    assert plan.days[0].activities[1].start == "11:20"
    assert not any(c.code == "activity_time_adjusted" for c in plan.checks)
    assert not any(c.severity == "error" for c in plan.checks)


def test_large_navigation_delay_is_repaired_jointly_and_records_correction():
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    tools = TravelTools(runtime())
    route = tools.route
    tools.route = lambda *args, **kwargs: route(*args, **kwargs).model_copy(update={"duration_minutes": 50})
    options = {
        "model_schedule": [day([stop("westlake", "10:00", "11:00"), stop("gushan_museum", "11:10", "12:10")])]
    }
    plan = validate_itinerary(req, build_plan(req, list(CATALOG), tools, options), options)
    assert plan.days[0].activities[1].start == "11:50"
    assert not any(c.code == "late_arrival" for c in plan.checks)
    assert any(c.code == "activity_time_adjusted" and "联合调整" in c.message for c in plan.checks)


def test_confirmed_timed_plan_persists_schedule_breaks_and_weather(client, monkeypatch):
    schedule = [
        day(
            [stop("gushan_museum", "10:00", "11:00"), stop("wulin", "19:00", "20:00")],
            breaks=[{"start": "12:00", "end": "13:00", "kind": "meal", "reason": "同区午餐"}],
        )
    ]
    select_reply(monkeypatch, [schedule], forecast())
    monkeypatch.setattr("app.main.request_settings", lambda *_: runtime())
    trip = client.post(
        "/api/trips", json={"start_date": "2026-10-27", "day_end": "23:00", "lodging_mode": "route_first"}
    ).json()
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    proposal = client.get(f"/api/runs/{run_id}").json()["proposal"]
    applied = client.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/apply",
        json={"base_version": 0, "idempotency_key": "timed-persist"},
    )
    assert applied.status_code == 200, applied.text
    saved = client.get(f"/api/trips/{trip['id']}").json()["itinerary"]
    assert saved["days"][0]["activities"][-1]["start"] == "19:00"
    assert saved["days"][0]["breaks"][0]["reason"] == "同区午餐"
    assert saved["days"][0]["weather_note"]
    assert saved["weather"]["data"]["days"][0]["reporttime"] == "2026-10-26 18:00:00"
    assert saved["planning_decision"]["source"] == "model_schedule"


def test_weather_review_unavailability_is_shown_without_fabricating_a_model_judgment(monkeypatch):
    calls = select_reply(monkeypatch, [[day([stop("westlake", "10:00", "11:15")])]], forecast())
    plan = generate(
        Requirements(start_date="2026-10-27", lodging_mode="route_first"), runtime(), lambda *_: None
    )
    assert len(calls) == 1
    assert not any(c.code == "weather_outdoor_risk" for c in plan.checks)
    assert any(c.code == "quality_review_unavailable" for c in plan.checks)


def test_weather_duplicate_rows_do_not_fake_full_date_coverage(monkeypatch):
    tools = TravelTools(runtime().model_copy(update={"tool_provider": "amap"}), adcode="330100")
    monkeypatch.setattr(
        tools,
        "_get",
        lambda *_: {
            "forecasts": [
                {
                    "reporttime": "2026-10-26 18:00:00",
                    "casts": [{"date": "2026-10-27", "dayweather": "小雨"}] * 2,
                }
            ]
        },
    )
    result = tools.weather(date(2026, 10, 27), 2)
    assert result.status == "partial" and len(result.data["days"]) == 1
    assert result.data["days"][0]["reporttime"] == "2026-10-26 18:00:00"


def test_timed_replanning_preserves_frozen_day_and_uses_new_explicit_times(monkeypatch):
    from test_replanning import fixture, move_intent, selection, tools

    from app.replanning import run_revision
    from app.schemas import ScheduleBreak, ScheduledDay

    req, base, catalog = fixture()
    base.planning_decision.source = "model_schedule"
    base.days[0].breaks = [ScheduleBreak(start="11:40", end="12:10", kind="meal", reason="已保存午餐")]
    frozen = base.days[0].model_dump()
    proposal = selection(
        daily_schedule=[
            ScheduledDay(**day([stop("p3", "09:00", "10:00"), stop("p5", "11:00", "12:00")], number=2)),
            ScheduledDay(**day([stop("p4", "09:00", "10:00"), stop("p2", "11:00", "12:00")], number=3)),
        ]
    )
    monkeypatch.setattr("app.replanning.model_json", lambda *_: proposal)
    _, result, _, _ = run_revision(req, base, move_intent(), tools(), catalog, message="把地点2移到第三天")
    assert result.days[0].model_dump() == frozen
    assert result.days[1].activities[1].start == "11:00"
    assert result.planning_decision.source == "model_schedule"
    assert not [c for c in result.checks if c.severity == "error"]


def test_delay_shifts_explicit_breaks_and_revision_context_keeps_weather(monkeypatch):
    from app.revisions import EditOperation, RevisionIntent, revise_route, revision_context

    schedule = [
        day(
            [stop("gushan_museum", "10:00", "11:00")],
            breaks=[{"start": "12:00", "end": "13:00", "kind": "meal", "reason": "午餐"}],
        )
    ]
    select_reply(monkeypatch, [schedule], forecast())
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    base = generate(req, runtime(), lambda *_: None)
    _, result = revise_route(
        req,
        base,
        RevisionIntent(summary="晚出发半小时", operations=[EditOperation(kind="delay", minutes=30)]),
        TravelTools(runtime()),
        CATALOG,
    )
    assert result.days[0].activities[0].start == "10:30"
    assert result.days[0].breaks[0].start == "12:30"
    assert revision_context(req, base, CATALOG)["weather"]["days"][0]["dayweather"] == "小雨"


def test_one_format_correction_uses_existing_catalog_and_returns_valid_schedule(monkeypatch):
    valid = {
        "place_ids": ["gushan_museum"],
        "daily_schedule": [day([stop("gushan_museum", "10:00", "11:00")])],
    }
    invalid = deepcopy(valid)
    invalid["daily_schedule"][0]["unexpected_date"] = "2026-10-27"
    messages = [
        {
            "tool_calls": [
                {"id": "catalog", "function": {"name": "search_places", "arguments": '{"query":""}'}}
            ]
        },
        {"content": json.dumps(invalid)},
        {"content": json.dumps(valid)},
    ]
    requests = []

    def post(url, **kwargs):
        requests.append(deepcopy(kwargs["json"]))
        return httpx.Response(
            200, json={"choices": [{"message": messages.pop(0)}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    output, trace = {}, []
    model_candidates(
        Requirements(start_date="2026-10-27"),
        runtime(),
        trace,
        lambda *_: None,
        recommendations=output,
        repair_format=True,
    )
    assert len(requests) == 3 and requests[-1]["tool_choice"] == "none"
    assert "schedule_format_errors" in requests[-1]["messages"][-1]["content"]
    assert output["daily_schedule"][0]["activities"][0]["start"] == "10:00"
    assert sum(t.get("tool") == "search_places" for t in trace) == 1


def test_worse_review_never_replaces_a_hard_feasible_preview(monkeypatch):
    from test_review_agent import fake_reviews, response

    fake_reviews(monkeypatch, [response(missing=True), response()])
    first = [day([stop("gushan_museum", "10:00", "11:00")])]
    second = [day([stop("gushan_museum", "00:00", "23:30")])]
    select_reply(monkeypatch, [first, second])
    plan = generate(
        Requirements(start_date="2026-10-27", pace="balanced", day_end="23:00", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
    )
    assert plan.days[0].activities[0].start == "10:00"
    assert not any(c.severity == "error" for c in plan.checks)
    assert plan.planning_decision.review_rounds == 1


def test_timed_model_cannot_move_fixed_appointment(monkeypatch):
    select_reply(monkeypatch, [[day([stop("gushan_museum", "10:00", "11:00")])]])
    req = Requirements(
        start_date="2026-10-27",
        lodging_mode="route_first",
        appointments=[{"place_id": "gushan_museum", "day": 1, "start": "14:00", "end": "15:00"}],
    )
    plan = generate(req, runtime(), lambda *_: None)
    assert plan.days[0].activities[0].locked
    assert any(c.code == "appointment_changed" and c.severity == "error" for c in plan.checks)
