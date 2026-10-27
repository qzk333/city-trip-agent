import json
from copy import deepcopy

import httpx
import pytest
from test_model_planning_decisions import runtime
from test_review_agent import fake_reviews, response
from test_timed_weather_planning import day, stop

from app.agent import generate
from app.catalog import CATALOG
from app.llm import MAX_MODEL_REQUESTS, model_candidates
from app.planning_output import PlanningOutputError, parse_planning_output
from app.schemas import Requirements
from app.tools import ToolUnavailable


def req(days=1):
    return Requirements(
        start_date="2026-10-27", days=days, day_end="23:00", pace="balanced", lodging_mode="route_first"
    )


def tool_reply():
    return {
        "tool_calls": [{"id": "catalog", "function": {"name": "search_places", "arguments": '{"query":""}'}}]
    }


def schedule_reply(schedule, **kwargs):
    return {"content": json.dumps({"daily_schedule": schedule, **kwargs})}


def replies(monkeypatch, messages):
    requests = []

    def post(url, **kwargs):
        requests.append(deepcopy(kwargs["json"]))
        return httpx.Response(
            200, json={"choices": [{"message": messages.pop(0)}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    return requests


def good_schedule():
    return [day([stop("gushan_museum", "10:00", "11:00"), stop("wulin", "19:00", "20:00")])]


@pytest.mark.parametrize(
    "bad,code",
    [
        ({"content": '{"daily_schedule": [broken}'}, "invalid_json"),
        ({"content": json.dumps([])}, "invalid_root"),
        ({"content": json.dumps({"place_ids": []})}, "candidate_index"),
        (schedule_reply([day([stop("made-up-id", "19:00", "20:00")])]), "unknown_place"),
        (
            schedule_reply([day([stop("wulin", "19:00", "18:00")])]),
            "schedule_schema",
        ),
        (schedule_reply([day([stop("wulin", "10:00", "11:00")], number=2)]), "day_order"),
    ],
)
def test_all_final_output_failures_reach_one_correction_without_another_tool_query(monkeypatch, bad, code):
    requests = replies(monkeypatch, [tool_reply(), bad, schedule_reply(good_schedule())])
    trace, output = [], {}
    result = model_candidates(
        req(), runtime(), trace, lambda *_: None, recommendations=output, repair_format=True
    )
    assert (
        result == ["gushan_museum", "wulin"]
        and output["daily_schedule"][0]["activities"][-1]["start"] == "19:00"
    )
    assert len(requests) == 3 <= MAX_MODEL_REQUESTS and requests[-1]["tool_choice"] == "none"
    errors = json.loads(requests[-1]["messages"][-1]["content"])["schedule_format_errors"]
    assert errors[0]["code"] == code and "field" in errors[0]
    assert sum(t.get("tool") == "search_places" for t in trace) == 1


def test_cross_day_revisits_are_preserved_with_a_unique_candidate_index():
    schedules = [
        day([stop("wulin", "10:00", "11:00"), stop("westlake", "19:00", "20:00")]),
        day(
            [
                stop("wulin", "10:00", "11:00"),
                stop("westlake", "16:00", "17:00"),
                stop("wulin", "19:00", "20:00"),
            ],
            number=2,
        ),
    ]
    parsed = parse_planning_output(json.dumps({"daily_schedule": schedules}), req(2), CATALOG)
    assert len(parsed["place_ids"]) == 2
    assert [len(d["activities"]) for d in parsed["daily_schedule"]] == [2, 3]


def test_second_day_schema_error_reports_array_index_and_does_not_log_rejected_value():
    schedules = [
        day([stop("wulin", "10:00", "11:00")]),
        day([stop("westlake", "PRIVATE_PROVIDER_VALUE", "20:00")], number=2),
    ]
    with pytest.raises(PlanningOutputError) as exc:
        parse_planning_output(json.dumps({"daily_schedule": schedules}), req(2), CATALOG)
    assert exc.value.issues[0]["field"] == "daily_schedule.1.activities.0.start"
    assert "PRIVATE_PROVIDER_VALUE" not in json.dumps(exc.value.issues)


def test_failed_correction_is_bounded_and_leaves_output_uncommitted(monkeypatch):
    bad = schedule_reply([day([stop("unknown-poi", "19:00", "20:00")])])
    calls = replies(monkeypatch, [tool_reply(), bad, bad])
    output = {}
    with pytest.raises(ToolUnavailable) as exc:
        model_candidates(req(), runtime(), [], lambda *_: None, recommendations=output, repair_format=True)
    assert exc.value.code == "invalid_schedule" and exc.value.details[0]["code"] == "unknown_place"
    assert len(calls) == 3 and not output


def test_review_failure_after_invalid_id_correction_is_saved_as_concrete_reason(monkeypatch):
    first = schedule_reply([day([stop("gushan_museum", "10:00", "11:00")])])
    bad = schedule_reply([day([stop("unknown-poi", "19:00", "20:00")])])
    calls = replies(monkeypatch, [tool_reply(), first, bad, bad])
    fake_reviews(monkeypatch, [response(missing=True)])
    plan = generate(req(), runtime(), lambda *_: None)
    decision = plan.planning_decision
    assert len(calls) == 4 and decision.review_outcome == "failed" and decision.selected_attempt == 1
    assert decision.review_failure.details[0].code == "unknown_place"
    assert plan.days[0].activities[0].place_id == "gushan_museum"
    assert any(c.code == "reviewer_evening" for c in plan.checks)


def test_reviewer_to_main_invalid_id_correction_produces_real_evening_activity(monkeypatch):
    first = schedule_reply([day([stop("gushan_museum", "10:00", "11:00")])])
    bad = schedule_reply([day([stop("unknown-poi", "19:00", "20:00")])])
    calls = replies(monkeypatch, [tool_reply(), first, bad, schedule_reply(good_schedule())])
    reviews = fake_reviews(monkeypatch, [response(missing=True), response()])
    plan = generate(req(), runtime(), lambda *_: None)
    assert len(calls) == 4 and len(reviews) == 2
    assert plan.days[0].activities[-1].start == "19:00"
    assert plan.planning_decision.review_outcome == "applied" and plan.planning_decision.selected_attempt == 2
    assert not plan.planning_decision.review_failure and not any(c.severity == "error" for c in plan.checks)
    assert any(t.get("tool") == "schedule_format_repair" for t in plan.trace)


def test_choice_uses_holistic_review_score_over_issue_count(monkeypatch):
    first = [
        day([stop("gushan_museum", "10:00", "11:00")]),
        day([stop("westlake", "10:00", "11:00")], number=2),
    ]
    second = deepcopy(first)
    second[1]["activities"].append(stop("wulin", "19:00", "20:00"))
    calls = replies(monkeypatch, [tool_reply(), schedule_reply(first), schedule_reply(second)])
    before = response(days=2, missing=True)
    before["overall_score"] = 40
    after = deepcopy(before)
    after["overall_score"] = 70
    after["days"][1]["evening"] = "scheduled"
    after["issues"] = [
        before["issues"][0],
        {**before["issues"][1], "category": "diversity", "place_ids": [], "alternative_ids": []},
        {**before["issues"][1], "category": "rationale", "place_ids": [], "alternative_ids": []},
    ]
    fake_reviews(monkeypatch, [before, after])
    plan = generate(req(2), runtime(), lambda *_: None)
    assert len([call for call in calls if "tools" in call]) == 3
    assert plan.planning_decision.selected_attempt == 2
    assert (
        plan.days[1].activities[-1].place_id == "wulin" and plan.planning_decision.review_outcome == "applied"
    )


def test_rewriting_only_rationale_is_not_reported_as_an_applied_timetable_change(monkeypatch):
    first = [day([stop("gushan_museum", "10:00", "11:00")])]
    second = deepcopy(first)
    second[0]["activities"][0]["reason"] = "修改推荐措辞，但没有落实新增晚间活动"
    replies(monkeypatch, [tool_reply(), schedule_reply(first), schedule_reply(second)])
    fake_reviews(monkeypatch, [response(missing=True)])
    plan = generate(req(), runtime(), lambda *_: None)
    assert plan.planning_decision.selected_attempt == 2
    assert plan.planning_decision.review_outcome == "unchanged"
    assert plan.planning_decision.quality_review.status == "changes_requested"


def test_changed_output_conflict_can_use_last_existing_request_budget_with_usage_ledger(monkeypatch):
    first = schedule_reply([day([stop("unknown-poi", "19:00", "20:00")])])
    second = schedule_reply([day([stop("westlake", "25:00", "26:00")])])
    calls = replies(monkeypatch, [tool_reply(), first, second, schedule_reply(good_schedule())])
    trace, output = [], {}
    model_candidates(req(), runtime(), trace, lambda *_: None, recommendations=output, repair_format=True)
    assert len(calls) == MAX_MODEL_REQUESTS == 4
    corrections = [t for t in trace if t.get("tool") == "schedule_format_repair"]
    assert len(corrections) == 2 and all(c["tool_choice"] == "none" for c in calls[2:])
    feedback = json.loads(calls[-1]["messages"][-1]["content"])
    assert feedback["selection_ledger"]["used"][0]["place_id"] == "westlake"
    assert len(feedback["selection_ledger"]["used"][0]["occurrences"]) == 1
    assert any(p["place_id"] == "wulin" for p in feedback["selection_ledger"]["unused"])
