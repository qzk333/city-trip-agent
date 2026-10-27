import json

from test_model_planning_decisions import runtime
from test_review_agent import fake_reviews, response
from test_timed_weather_planning import day, select_reply, stop

from app.agent import generate
from app.catalog import CATALOG
from app.planner import build_plan, clock
from app.planning_output import parse_planning_output
from app.schemas import AttributeEvidence, PlanningDecision, Requirements
from app.tools import TravelTools
from app.validation import validate_itinerary


def place(pid, **changes):
    return CATALOG["westlake"].model_copy(
        update={
            "id": pid,
            "name": f"地点{pid}",
            "opening": None,
            "closing": None,
            "longitude": 120.16,
            "latitude": 30.25,
            **changes,
        }
    )


def check(schedule, catalog, **changes):
    req = Requirements(start_date="2026-10-27", day_end="23:00", lodging_mode="route_first", **changes)
    options = {"model_schedule": schedule}
    plan = build_plan(req, list(catalog), TravelTools(runtime()), options, catalog)
    plan.planning_decision = PlanningDecision(source="model_schedule")
    return req, validate_itinerary(req, plan, options, catalog)


def test_more_than_twelve_short_activities_are_not_rejected_by_a_business_quota():
    catalog = {f"p{i}": place(f"p{i}") for i in range(13)}
    schedule = [day([stop(pid, clock(540 + i * 35), clock(560 + i * 35)) for i, pid in enumerate(catalog)])]
    req, plan = check(
        schedule,
        catalog,
        must_visit=list(catalog),
        appointments=[
            {"place_id": a["place_id"], "day": 1, "start": a["start"], "end": a["end"]}
            for a in schedule[0]["activities"]
        ],
    )
    parsed = parse_planning_output(json.dumps({"daily_schedule": schedule}), req, catalog)
    assert len(parsed["daily_schedule"][0]["activities"]) == 13
    assert len(plan.days[0].activities) == 13
    assert not any(c.severity == "error" for c in plan.checks)


def test_revisits_have_independent_ids_and_use_actual_incoming_routes_by_visit():
    catalog = {pid: place(pid) for pid in ("a", "b")}
    req = Requirements(start_date="2026-10-27", day_end="23:00", lodging_mode="route_first")
    tools = TravelTools(runtime())
    original = tools.route
    tools.route = lambda origin, p, destination, q, mode: original(
        origin, p, destination, q, mode
    ).model_copy(update={"duration_minutes": 5 if destination == "b" else 45})
    schedule = [
        day(
            [
                stop("a", "09:00", "10:00"),
                stop("b", "11:00", "12:00"),
                {**stop("a", "18:30", "19:30"), "reason": "重访同一公园观察不同光线"},
            ]
        )
    ]
    options = {"model_schedule": schedule}
    plan = validate_itinerary(req, build_plan(req, list(catalog), tools, options, catalog), options, catalog)
    visits = plan.days[0].activities
    assert len({a.id for a in visits}) == 3
    assert visits[0].start == "09:00" and visits[2].start == "18:30"
    assert [leg.duration_minutes for leg in plan.days[0].legs] == [5, 45]
    assert not any(c.severity == "error" for c in plan.checks)


def test_time_intent_and_model_suggested_windows_do_not_rewrite_valid_times():
    catalog = {"a": place("a")}
    schedule = [
        day(
            [
                {
                    **stop("a", "18:15", "18:45"),
                    "time_intent": "night",
                    "earliest_start": "19:00",
                    "latest_end": "17:00",
                }
            ]
        )
    ]
    _, plan = check(schedule, catalog)
    assert plan.days[0].activities[0].start == "18:15"
    assert plan.days[0].activities[0].end == "18:45"
    assert not any(c.severity == "error" for c in plan.checks)


def test_travel_margin_is_advice_while_actual_unreachable_transfer_remains_an_error():
    catalog = {pid: place(pid) for pid in ("a", "b")}
    req = Requirements(start_date="2026-10-27", day_end="23:00", lodging_mode="route_first")
    tools = TravelTools(runtime())
    original = tools.route
    tools.route = lambda *a: original(*a).model_copy(update={"duration_minutes": 5})
    options = {"model_schedule": [day([stop("a", "10:00", "11:00"), stop("b", "11:05", "12:00")])]}
    plan = validate_itinerary(req, build_plan(req, list(catalog), tools, options, catalog), options, catalog)
    assert not any(c.severity == "error" for c in plan.checks)
    assert any(c.code == "transfer_buffer_low" and c.severity == "warning" for c in plan.checks)
    plan.days[0].activities[1].start = "11:04"
    plan = validate_itinerary(req, plan, options, catalog)
    assert any(c.code == "late_arrival" and c.severity == "error" for c in plan.checks)


def test_unknown_opening_window_is_not_a_hard_fact_but_observed_hours_remain_checked():
    p = place("a", fixture=False, opening="09:00", closing="17:00")
    schedule = [day([stop("a", "19:00", "20:00")])]
    _, plan = check(schedule, {p.id: p})
    assert any(c.code == "opening_hours_unverified" and c.severity == "warning" for c in plan.checks)
    assert not any(c.severity == "error" for c in plan.checks)
    verified = p.model_copy(deep=True)
    verified.traits.evidence.append(
        AttributeEvidence(
            field="opening_hours",
            value="09:00—17:00",
            kind="observed",
            source_url="https://venue.example/opening",
        )
    )
    _, plan = check(schedule, {verified.id: verified})
    assert any(c.code == "opening_hours" and c.severity == "error" for c in plan.checks)


def test_rest_only_day_is_not_forced_to_visit_a_place_but_required_stop_still_matters():
    catalog = {"a": place("a")}
    schedule = [
        day([], breaks=[{"start": "10:00", "end": "16:00", "kind": "rest", "reason": "用户要求休整"}])
    ]
    req, plan = check(schedule, catalog)
    parsed = parse_planning_output(json.dumps({"daily_schedule": schedule}), req, catalog)
    assert parsed["place_ids"] == [] and not any(c.severity == "error" for c in plan.checks)
    req.must_visit = ["a"]
    plan = validate_itinerary(req, plan, catalog=catalog)
    assert any(c.code == "missing_required" and c.severity == "error" for c in plan.checks)


def test_holistic_score_can_reject_a_night_fix_that_makes_the_whole_day_worse(monkeypatch):
    first = [day([stop("gushan_museum", "10:00", "12:00"), stop("westlake", "14:00", "17:00")])]
    second = [day([stop("gushan_museum", "10:00", "11:00"), stop("wulin", "19:00", "20:00")])]
    select_reply(monkeypatch, [first, second])
    before = response()
    before["overall_score"] = 85.5
    before["issues"] = [
        {
            "category": "rationale",
            "day": 1,
            "place_ids": [],
            "alternative_ids": [],
            "evidence": "推荐依据仍可细化。",
            "suggestion": "补充取舍说明。",
        }
    ]
    after = response()
    after["overall_score"] = 45.5
    after["days"][0]["evening"] = "scheduled"
    fake_reviews(monkeypatch, [before, after])
    req = Requirements(start_date="2026-10-27", day_end="23:00", pace="balanced", lodging_mode="route_first")
    plan = generate(req, runtime(), lambda *_: None)
    assert plan.planning_decision.selected_attempt == 1
    assert plan.days[0].activities[-1].start == "14:00"


def test_conflicting_two_appointments_at_same_venue_keep_distinct_fixed_ids():
    catalog = {"a": place("a")}
    appointments = [
        {"place_id": "a", "day": 1, "start": "10:00", "end": "11:00"},
        {"place_id": "a", "day": 1, "start": "15:00", "end": "16:00"},
    ]
    _, plan = check(
        [day([stop("a", "10:00", "11:00"), stop("a", "15:00", "16:00")])], catalog, appointments=appointments
    )
    assert [a.id for a in plan.days[0].activities] == ["d1-a-fixed0", "d1-a-fixed1"]
    assert not any(c.severity == "error" for c in plan.checks)


def test_rest_day_still_requires_a_transfer_between_different_confirmed_hotels():
    from app.lodging import hotel_candidate
    from app.schemas import HotelNight, HotelResearch, Location

    req = Requirements(start_date="2026-10-27", days=3, lodging_mode="route_first")
    anchor = place("a")
    hotels = [
        hotel_candidate(
            f"h{i}",
            Location(name=f"酒店{i}", longitude=120.1 + i * 0.01, latitude=30.25),
            anchor,
            anchor,
            provider="demo",
        )
        for i in (1, 2)
    ]
    lodging = HotelResearch(
        status="ready",
        nights=[
            HotelNight(
                night=i,
                checkin=f"2026-10-{26 + i}",
                checkout=f"2026-10-{27 + i}",
                last_place_id="a",
                next_place_id="a",
                candidates=[hotel],
                selected_id=hotel.id,
            )
            for i, hotel in enumerate(hotels, start=1)
        ],
    )
    options = {"model_schedule": [day([], number=i) for i in (1, 2, 3)], "lodging": lodging.model_dump()}
    plan = validate_itinerary(
        req, build_plan(req, ["a"], TravelTools(runtime()), options, {"a": anchor}), options, {"a": anchor}
    )
    assert len(plan.days[1].legs) == 1
    assert plan.days[1].legs[0].origin_id == "hotel-night-1"
    assert plan.days[1].legs[0].destination_id == "hotel-night-2"
    assert not any(c.severity == "error" for c in plan.checks)
    plan.days[1].legs = []
    checked = validate_itinerary(req, plan, options, {"a": anchor})
    assert any(c.code == "route_coverage" and c.severity == "error" for c in checked.checks)


def test_equivalent_time_format_and_redundant_indexes_do_not_discard_a_valid_plan():
    req = Requirements(start_date="2026-10-27")
    payload = {
        "daily_schedule": [day([stop("westlake", "9:00:00", "10:00:00")])],
        "place_ids": ["unknown-unused-index"],
        "day_groups": "redundant-format-error",
    }
    raw = "```json\n" + json.dumps(payload) + "\n```"
    parsed = parse_planning_output(raw, req, CATALOG)
    assert parsed["daily_schedule"][0]["activities"][0]["start"] == "09:00"
    assert parsed["daily_schedule"][0]["activities"][0]["end"] == "10:00"
    assert parsed["place_ids"] == ["westlake"] and parsed["day_groups"] == [["westlake"]]


def test_ambiguous_natural_edit_of_revisits_does_not_silently_modify_the_wrong_visit():
    import pytest

    from app.revisions import EditOperation, RevisionIntent, revise_route
    from app.tools import ToolUnavailable

    catalog = {"a": place("a")}
    req, plan = check([day([stop("a", "10:00", "11:00"), stop("a", "18:00", "19:00")])], catalog)
    original = plan.model_dump()
    intent = RevisionIntent(
        summary="删除公园", operations=[EditOperation(kind="remove", day=1, place_id="a")]
    )
    with pytest.raises(ToolUnavailable, match="多次到访"):
        revise_route(req, plan, intent, TravelTools(runtime()), catalog)
    assert plan.model_dump() == original


def test_card_removal_preserves_the_other_visit_and_its_identity():
    from app.schemas import ProposalRequest
    from app.services import make_overrides

    catalog = {pid: place(pid) for pid in ("a", "b")}
    req, base = check(
        [day([stop("a", "10:00", "11:00"), stop("b", "12:00", "13:00"), stop("a", "18:00", "19:00")])],
        catalog,
    )
    first, _, last = base.days[0].activities
    last.locked = True
    preserved_id = last.id
    request = ProposalRequest(base_version=1, action="remove", day=1, activity_id=first.id)
    options = make_overrides(base.model_dump(), request)
    plan = validate_itinerary(
        req, build_plan(req, list(catalog), TravelTools(runtime()), options, catalog), options, catalog
    )
    assert [(a.place_id, a.start) for a in plan.days[0].activities] == [("b", "12:00"), ("a", "18:00")]
    assert plan.days[0].activities[-1].id == preserved_id and plan.days[0].activities[-1].locked
    assert not any(c.severity == "error" for c in plan.checks)


def test_explicit_add_of_a_second_visit_is_preserved_by_timed_replanning(monkeypatch):
    from app.replanning import DaySelection, RepairPlan, run_revision
    from app.revisions import EditOperation, RevisionIntent
    from app.schemas import ScheduledDay

    catalog = {pid: place(pid) for pid in ("a", "b")}
    req, base = check(
        [day([stop("a", "10:00", "11:00")]), day([stop("b", "10:00", "11:00")], number=2)], catalog, days=2
    )
    intent = RevisionIntent(
        summary="第二天晚上再去地点a", operations=[EditOperation(kind="add", day=2, place_id="a")]
    )
    proposal = RepairPlan(
        summary="保留首次参观并安排次日晚间重访。",
        days=[DaySelection(day=1, place_ids=["a"]), DaySelection(day=2, place_ids=["b", "a"])],
        daily_schedule=[
            ScheduledDay(**day([stop("a", "10:00", "11:00")])),
            ScheduledDay(**day([stop("b", "10:00", "11:00"), stop("a", "18:00", "19:00")], number=2)),
        ],
    )
    monkeypatch.setattr("app.replanning.model_json", lambda *_: proposal)
    _, plan, _, _ = run_revision(
        req, base, intent, TravelTools(runtime()), catalog, message="第二天晚上再去地点a"
    )
    assert sum(a.place_id == "a" for d in plan.days for a in d.activities) == 2
    assert plan.days[1].activities[-1].start == "18:00"
    assert not any(c.severity == "error" for c in plan.checks)
