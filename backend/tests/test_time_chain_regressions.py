from test_candidate_quality import poi
from test_model_planning_decisions import runtime
from test_timed_weather_planning import day, stop

from app.planner import build_plan
from app.schemas import PlanningDecision, Requirements, RouteLeg, TravelProfile
from app.tools import TravelTools
from app.validation import validate_itinerary


def run_plan(activities, breaks=None, appointments=None):
    catalog = {
        p.id: p
        for p in [
            poi("bay", "深圳湾公园", "nature"),
            poi("bridge", "深圳湾观海栈桥", "nature"),
            poi("view", "观桥公园", "nature"),
        ]
    }
    req = Requirements(
        start_date="2026-10-27",
        lodging_mode="route_first",
        day_end="23:00",
        profile=TravelProfile(themes=["night"]),
        appointments=appointments or [],
    )
    tools = TravelTools(runtime())

    def route(origin_id, origin, destination_id, destination, mode):
        return RouteLeg(
            origin_id=origin_id,
            destination_id=destination_id,
            duration_minutes=27 if origin_id == "amap-bay" else 8,
            distance_meters=1000,
            provider="demo",
            evidence="固定测试路段",
        )

    tools.route = route
    options = {"model_schedule": [day(activities, breaks=breaks)]}
    result = build_plan(req, list(catalog), tools, options, catalog)
    result.planning_decision = PlanningDecision(source="model_schedule")
    return validate_itinerary(req, result, options, catalog), req, catalog


def test_explicit_daytime_transition_never_forced_to_19_and_late_rest_remains_disjoint():
    activities = [
        {
            **stop("amap-bay", "15:45", "17:00"),
            "time_intent": "daytime",
            "reason": "不声称已核实日落，只作为白天到入夜的过渡。",
        },
        {**stop("amap-bridge", "19:00", "20:20"), "time_intent": "night"},
        {**stop("amap-view", "20:40", "22:00"), "time_intent": "night"},
    ]
    breaks = [
        {"start": "17:00", "end": "19:00", "kind": "meal", "reason": "晚餐休息，交通另计"},
        {"start": "22:00", "end": "23:00", "kind": "free", "reason": "自由返回"},
    ]
    result, _, _ = run_plan(activities, breaks)
    assert result.days[0].activities[0].start == "15:45"
    assert result.days[0].activities[1].start == "19:00"
    assert not any(c.severity == "error" for c in result.checks)
    assert not any(
        c.code == "recommendation_time_mismatch" and c.activity_id == result.days[0].activities[0].id
        for c in result.checks
    )


def test_cascaded_late_night_chain_is_fit_backwards_before_end_of_day():
    activities = [
        {**stop("amap-bay", "15:45", "17:00"), "time_intent": "daytime"},
        {**stop("amap-bridge", "20:52", "22:12"), "time_intent": "night"},
        {**stop("amap-view", "20:40", "22:00"), "time_intent": "night"},
    ]
    result, _, _ = run_plan(
        activities, [{"start": "22:00", "end": "23:00", "kind": "free", "reason": "夜游后的自由活动"}]
    )
    bridge, view = result.days[0].activities[-2:]
    assert bridge.end <= view.start and view.end <= "23:00"
    assert result.days[0].activities[-1].end == "23:00"
    assert not any(c.severity == "error" for c in result.checks)
    assert any(c.code == "activity_time_adjusted" and "联合调整" in c.message for c in result.checks)


def test_joint_repair_respects_fixed_final_appointment():
    activities = [
        {**stop("amap-bay", "15:45", "17:00"), "time_intent": "daytime"},
        {**stop("amap-bridge", "20:52", "22:12"), "time_intent": "night"},
        {**stop("amap-view", "20:40", "22:00"), "time_intent": "night"},
    ]
    result, _, _ = run_plan(
        activities, appointments=[{"place_id": "amap-view", "day": 1, "start": "20:40", "end": "22:00"}]
    )
    assert result.days[0].activities[-1].start == "20:40" and result.days[0].activities[-1].locked
    assert not any(c.severity == "error" for c in result.checks)


def test_unfixable_fixed_chain_stays_blocked_but_rest_uses_union_of_all_activities():
    activities = [
        {**stop("amap-bay", "15:45", "17:00"), "time_intent": "daytime"},
        {**stop("amap-bridge", "20:52", "22:12"), "time_intent": "night"},
        {**stop("amap-view", "20:40", "22:00"), "time_intent": "night"},
    ]
    appointments = [
        {"place_id": row["place_id"], "day": 1, "start": row["start"], "end": row["end"]}
        for row in activities[1:]
    ]
    result, _, _ = run_plan(
        activities, [{"start": "22:00", "end": "23:00", "kind": "free", "reason": "自由活动"}], appointments
    )
    assert any(c.code == "late_arrival" for c in result.checks)
    assert any(c.code == "time_chain_repair_incomplete" for c in result.checks)
    assert not any(c.code == "break_overlap" for c in result.checks)
    assert result.days[0].activities[-1].start == "20:40"


def test_model_suggested_window_does_not_block_physical_time_chain_repair():
    activities = [
        {**stop("amap-bay", "15:45", "17:00"), "time_intent": "daytime"},
        {**stop("amap-bridge", "20:52", "22:12"), "time_intent": "night", "earliest_start": "20:52"},
        {**stop("amap-view", "20:40", "22:00"), "time_intent": "night", "latest_end": "22:00"},
    ]
    result, _, _ = run_plan(activities)
    assert not any(c.severity == "error" for c in result.checks)
    assert any(c.code == "activity_time_adjusted" for c in result.checks)


def test_sunset_and_night_tags_do_not_impose_fixed_clock_boundaries():
    activities = [
        {
            **stop("amap-bay", "16:45", "18:15"),
            "time_intent": "sunset",
            "reason": "从白天到入夜的过渡，日落未知。",
        },
        {**stop("amap-bridge", "18:45", "20:15"), "time_intent": "night"},
        {**stop("amap-view", "20:30", "21:45"), "time_intent": "night"},
    ]
    result, _, _ = run_plan(
        activities, [{"start": "21:45", "end": "23:00", "kind": "free", "reason": "返程余量"}]
    )
    assert result.days[0].activities[0].start == "16:45"
    assert result.days[0].activities[1].start == "18:45"
    assert not any(c.severity == "error" for c in result.checks)
