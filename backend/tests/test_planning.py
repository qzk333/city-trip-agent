import pytest
from pydantic import ValidationError

from app.agent import generate
from app.config import Settings
from app.schemas import Requirements
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def plan(requirements, **config):
    return generate(requirements, Settings(_env_file=None, **config), lambda *_: None)


def test_demo_is_a_validated_estimate_not_verified_travel_advice(requirements):
    result = plan(requirements)
    assert result.status == "NEEDS_CONFIRMATION"
    assert not [c for c in result.checks if c.severity == "error"]
    visited = {a.place_id for d in result.days for a in d.activities}
    assert set(requirements.must_visit) <= visited
    assert result.weather.status == "partial"
    assert result.weather.data["days"] == []


def test_fixed_appointments_are_not_silently_moved():
    req = Requirements(
        start_date="2026-10-24",
        appointments=[
            {"place_id": "gushan_museum", "start": "14:00", "end": "15:30"},
        ],
    )
    result = plan(req)
    fixed = next(a for a in result.days[0].activities if a.locked)
    assert (fixed.start, fixed.end) == ("14:00", "15:30")
    assert not [c for c in result.checks if c.severity == "error"]


def test_overlapping_appointments_are_a_conflict():
    req = Requirements(
        start_date="2026-10-24",
        appointments=[
            {"place_id": "gushan_museum", "start": "14:00", "end": "15:30"},
            {"place_id": "art_museum", "start": "15:00", "end": "16:00"},
        ],
    )
    result = plan(req)
    assert result.status == "CONFLICT"
    assert any(c.code == "fixed_overlap" for c in result.checks)


def test_search_failure_does_not_claim_mathematical_infeasibility():
    result = plan(
        Requirements(
            start_date="2026-10-24", day_start="09:00", day_end="09:10", must_visit=["gushan_museum"]
        )
    )
    assert result.status == "NO_SOLUTION_WITHIN_LIMIT"
    assert any(c.code == "missing_required" for c in result.checks)


def test_exhausted_tool_budget_cannot_produce_a_confirmed_plan(requirements):
    result = plan(requirements, max_route_calls=1)
    assert result.status == "NO_SOLUTION_WITHIN_LIMIT"
    assert any(c.code == "route_unavailable" for c in result.checks)


def test_validator_detects_a_tampered_arrival(requirements):
    result = plan(requirements)
    result.days[0].activities[0].start = "09:00"
    checked = validate_itinerary(requirements, result)
    assert any(c.code == "late_arrival" for c in checked.checks)
    assert checked.status == "NO_SOLUTION_WITHIN_LIMIT"


def test_three_day_plan_has_correct_dates_and_complete_return_routes():
    req = Requirements(start_date="2026-10-24", days=3)
    result = plan(req)
    assert [d.date for d in result.days] == ["2026-10-24", "2026-10-25", "2026-10-26"]
    assert all(len(d.legs) == len(d.activities) + 1 for d in result.days)
    assert not [c for c in result.checks if c.severity == "error"]


def test_real_provider_missing_key_never_falls_back_to_demo(requirements):
    tools = TravelTools(Settings(tool_provider="amap", amap_api_key="", _env_file=None))
    with pytest.raises(ToolUnavailable, match="Key"):
        tools.route("a", requirements.hotel, "b", requirements.hotel, "walking")
    assert tools.cache == {}


def test_invalid_model_configuration_is_explicit(requirements):
    with pytest.raises(ToolUnavailable, match="LLM"):
        plan(requirements, planning_mode="llm", llm_base_url="", llm_api_key="", llm_model="")


def test_cross_midnight_input_is_rejected():
    with pytest.raises(ValidationError):
        Requirements(start_date="2026-10-24", day_start="18:00", day_end="09:00")


def test_mixed_timezone_input_is_a_validation_error():
    with pytest.raises(ValidationError):
        Requirements(start_date="2026-10-24", day_start="09:00+08:00")
