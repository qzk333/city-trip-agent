"""Hotel boundaries must be fitted jointly with activities, breaks and user anchors."""

from collections import Counter

import pytest

from app.catalog import CATALOG
from app.config import Settings
from app.hotel_routes import select_hotel
from app.lodging import hotel_candidate
from app.planner import build_plan
from app.revisions import HotelReview, review_hotel_choice
from app.schemas import (
    HotelChoiceRequest,
    HotelNight,
    HotelResearch,
    PlanningDecision,
    Requirements,
    RouteLeg,
    ScheduledDay,
)
from app.tools import TravelTools
from app.validation import validate_itinerary


def setup_case(*, locked=False, day_end="23:00", returning=2):
    req = Requirements(start_date="2026-10-27", days=3, day_end=day_end, lodging_mode="route_first")
    catalog = {
        pid: CATALOG["westlake"].model_copy(update={"id": pid, "name": pid, "opening": None, "closing": None})
        for pid in "abcd"
    }
    tools = TravelTools(Settings(_env_file=None))
    calls = []

    def route(origin, _a, destination, _b, _mode):
        calls.append((origin, destination))
        duration = 20 if origin == "hotel-night-1" else returning if destination == "hotel-night-1" else 18
        return RouteLeg(
            origin_id=origin,
            destination_id=destination,
            duration_minutes=duration,
            distance_meters=1000,
            provider="demo",
            evidence="saved test route",
        )

    tools.route = route

    def stop(pid, start, end):
        return {"place_id": pid, "start": start, "end": end}

    schedule = [
        {"day": 1, "activities": [stop("a", "19:15", "20:15")]},
        {
            "day": 2,
            "activities": [stop("b", "09:00", "10:45"), stop("c", "13:00", "14:40")],
            "breaks": [
                {"start": "10:45", "end": "12:10", "kind": "rest", "reason": "晨间游览后休息"},
                {"start": "12:10", "end": "12:42", "kind": "meal", "reason": "午餐"},
            ],
        },
        {"day": 3, "activities": [stop("d", "10:00", "11:00")]},
    ]
    base = build_plan(req, list(catalog), tools, {"model_schedule": schedule}, catalog)
    base.planning_decision = PlanningDecision(source="model_schedule")
    nights = []
    for night, left, right in [(1, "a", "b"), (2, "c", "d")]:
        candidate = hotel_candidate(f"h{night}", req.hotel, catalog[left], catalog[right], provider="demo")
        nights.append(
            HotelNight(
                night=night,
                checkin=base.days[night - 1].date,
                checkout=base.days[night].date,
                last_place_id=left,
                next_place_id=right,
                candidates=[candidate],
            )
        )
    base.lodging = HotelResearch(status="ready", nights=nights)
    base.days[1].activities[0].locked = locked
    base = validate_itinerary(req, base, catalog=catalog)
    assert not [c for c in base.checks if c.severity == "error"]
    calls.clear()
    return req, base, tools, catalog, calls


def choose(req, base, tools, catalog):
    return select_hotel(
        req, base, HotelChoiceRequest(base_version=1, night=1, candidate_id="h1"), tools, catalog
    )


def schedule_of(day):
    return ScheduledDay(
        day=day.day,
        activities=[
            a.model_dump(include={"place_id", "start", "end", "reason", "time_intent"})
            for a in day.activities
        ],
        breaks=day.breaks,
        weather_note=day.weather_note,
    )


def test_hotel_commute_moves_rest_with_delayed_visit_without_mutating_saved_route():
    req, base, tools, catalog, calls = setup_case()
    before = base.model_dump()
    changed = choose(req, base, tools, catalog)
    first = changed.days[1].activities[0]
    assert (first.start, first.end) == ("09:20", "11:05")
    assert changed.days[1].breaks[0].start == "11:05"
    assert changed.days[1].breaks[1].start == "12:10"
    assert not [c for c in changed.checks if c.severity == "error"]
    assert any(c.code == "break_adjusted" for c in changed.checks)
    assert base.model_dump() == before
    assert changed.days[2] == base.days[2]
    assert changed.days[1].legs[1:] == base.days[1].legs
    assert calls == [("a", "hotel-night-1"), ("hotel-night-1", "b")]


def test_return_to_hotel_can_move_flexible_last_visit_earlier_within_user_window():
    req, base, tools, catalog, _ = setup_case(day_end="20:30", returning=30)
    changed = choose(req, base, tools, catalog)
    assert changed.days[0].activities[0].end == "20:00"
    assert not [c for c in changed.checks if c.severity == "error"]


def test_repeated_internal_routes_are_reused_by_visit_order():
    req, base, tools, catalog, calls = setup_case()
    day = base.days[1]
    b, c = day.activities
    day.activities = [
        b.model_copy(update={"start": "09:00", "end": "09:30"}),
        c.model_copy(update={"start": "10:00", "end": "10:30"}),
        b.model_copy(update={"id": b.id + "-visit2", "start": "11:00", "end": "11:30"}),
        c.model_copy(update={"id": c.id + "-visit2"}),
    ]
    day.breaks[0].start = "11:30"
    day.legs = [
        RouteLeg(
            origin_id=a,
            destination_id=b,
            duration_minutes=duration,
            distance_meters=1000,
            provider="demo",
            evidence="distinct saved visit",
        )
        for a, b, duration in [("b", "c", 5), ("c", "b", 9), ("b", "c", 17)]
    ]
    assert not [c for c in validate_itinerary(req, base, catalog=catalog).checks if c.severity == "error"]
    changed = choose(req, base, tools, catalog)
    assert changed.days[1].legs[1:] == day.legs
    assert [leg.duration_minutes for leg in changed.days[1].legs[1:]] == [5, 9, 17]
    assert calls == [("a", "hotel-night-1"), ("hotel-night-1", "b")]
    assert not [c for c in changed.checks if c.severity == "error"]


def test_rest_day_at_same_hotel_does_not_add_an_unneeded_transfer():
    req, base, tools, catalog, calls = setup_case()
    base.days[1].activities, base.days[1].legs, base.days[1].breaks = [], [], []
    base.lodging.nights[1].selected_id = "h2"
    base.days[2].legs = [tools.route("hotel-night-2", req.hotel, "d", catalog["d"], req.transport)]
    calls.clear()
    changed = choose(req, base, tools, catalog)
    assert changed.days[1].legs == []
    assert not [c for c in changed.checks if c.severity == "error"]
    assert calls == [("a", "hotel-night-1")]


def test_unreachable_locked_visit_stays_locked_and_model_keep_is_not_success(monkeypatch):
    req, base, tools, catalog, _ = setup_case(locked=True)
    changed = choose(req, base, tools, catalog)
    assert changed.days[1].activities[0] == base.days[1].activities[0]
    assert any(c.code == "late_arrival" and c.severity == "error" for c in changed.checks)
    tools.settings.planning_mode = "llm"
    monkeypatch.setattr(
        "app.revisions.model_json", lambda *_: HotelReview(decision="keep", rationale="保持原样")
    )
    reviewed = review_hotel_choice(req, changed, 1, tools, catalog)
    assert reviewed.revision_status == "unavailable"
    assert any(c.code == "late_arrival" and c.severity == "error" for c in reviewed.checks)


def test_hotel_model_can_propose_concrete_times_and_meals_and_freeze_other_days(monkeypatch):
    req, base, tools, catalog, _ = setup_case()
    changed = choose(req, base, tools, catalog)
    schedule = [schedule_of(d) for d in changed.days[:2]]
    schedule[1].activities[0].start, schedule[1].activities[0].end = "09:30", "11:15"
    schedule[1].breaks[0].start = "11:15"
    tools.settings.planning_mode = "llm"
    monkeypatch.setattr(
        "app.revisions.model_json",
        lambda *_: HotelReview(
            decision="adjust", rationale="住宿后预留更舒适的出发时刻", daily_schedule=schedule
        ),
    )
    result = review_hotel_choice(req, changed, 1, tools, catalog)
    assert result.revision_status == "model_reviewed"
    assert result.days[1].activities[0].start == "09:30"
    assert result.days[1].breaks[0].start == "11:15"
    assert result.days[2] == base.days[2]
    assert result.lodging.nights[0].selected_id == "h1"
    assert Counter(a.place_id for d in result.days for a in d.activities) == Counter("abcd")
    assert not [c for c in result.checks if c.severity == "error"]


@pytest.mark.parametrize("invalid", ["drop_visit", "outside_scope", "locked_time", "unresolved_conflict"])
def test_invalid_model_hotel_schedule_keeps_locally_checked_preview(monkeypatch, invalid):
    req, base, tools, catalog, _ = setup_case(locked=invalid in {"locked_time", "unresolved_conflict"})
    changed = choose(req, base, tools, catalog)
    schedule = [schedule_of(d) for d in changed.days[:2]]
    if invalid == "drop_visit":
        schedule[1].activities.pop()
    elif invalid == "outside_scope":
        schedule.append(schedule_of(changed.days[2]))
    elif invalid == "locked_time":
        schedule[1].activities[0].start, schedule[1].activities[0].end = "10:00", "11:45"
    else:
        # A new timetable is not successful while the original unreachable
        # locked visit remains unresolved.
        schedule[1].activities[1].start, schedule[1].activities[1].end = "23:00", "23:50"
    before = [d.model_dump() for d in changed.days]
    tools.settings.planning_mode = "llm"
    monkeypatch.setattr(
        "app.revisions.model_json",
        lambda *_: HotelReview(decision="adjust", rationale="修改时间表", daily_schedule=schedule),
    )
    result = review_hotel_choice(req, changed, 1, tools, catalog)
    assert result.revision_status == "unavailable"
    assert [d.model_dump() for d in result.days] == before
