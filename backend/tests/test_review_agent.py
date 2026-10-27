import json
from copy import deepcopy
from time import monotonic

import httpx
import pytest
from sqlalchemy import select
from test_model_planning_decisions import runtime
from test_timed_weather_planning import day, select_reply, stop

from app.agent import generate
from app.catalog import CATALOG
from app.database import AgentTaskRecord
from app.planner import build_plan
from app.review_agent import (
    audit_schedule,
    current_review,
    review_input,
)
from app.review_agent import (
    review_schedule as actual_review_schedule,
)
from app.schemas import Requirements, ScheduleReview, ScheduleReviewAnalysis
from app.tools import TravelTools
from app.validation import validate_itinerary


def request(days=1):
    return Requirements(
        start_date="2026-10-27", days=days, day_end="23:00", pace="balanced", lodging_mode="route_first"
    )


def draft(req=None, schedule=None):
    req = req or request()
    plan = build_plan(
        req,
        list(CATALOG),
        TravelTools(runtime()),
        {"model_schedule": schedule or [day([stop("gushan_museum", "10:00", "11:00")])]},
    )
    plan.planning_decision.source = "model_schedule"
    return req, validate_itinerary(req, plan)


def response(days=1, *, missing=False):
    return {
        "summary": "每晚仍需比较同区体验。" if missing else "逐日安排合理，保留有依据的休息。",
        "days": [
            {
                "day": n,
                "evening": "missing" if missing else "rest_justified",
                "pace": "appropriate",
                "explanation": "18:00后到23:00未安排具体活动。"
                if missing
                else "用户需要低强度休息，不要求占满结束时间。",
            }
            for n in range(1, days + 1)
        ],
        "issues": [
            {
                "category": "evening",
                "day": n,
                "place_ids": [],
                "alternative_ids": ["wulin"],
                "evidence": "18:00—23:00没有具体活动，多探索需求尚未落实。",
                "suggestion": "比较同区武林街区19:00—20:00体验，留出晚餐和转场。",
            }
            for n in range(1, days + 1)
        ]
        if missing
        else [],
    }


def mocked_post(monkeypatch, payload):
    captured = []

    def post(url, **kwargs):
        captured.append(deepcopy(kwargs))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.review_agent.httpx.post", post)
    return captured


def fake_reviews(monkeypatch, responses):
    calls = []

    def run(task_input, *_args, **_kwargs):
        calls.append(deepcopy(task_input))
        analysis = ScheduleReviewAnalysis.model_validate(responses[min(len(calls) - 1, len(responses) - 1)])
        return ScheduleReview(
            status="changes_requested" if analysis.issues else "passed", **analysis.model_dump()
        ), {"tool": "schedule_reviewer", "status": "ok"}

    monkeypatch.setattr("app.review_agent.review_schedule", run)
    return calls


def test_reviewer_gets_actual_days_weather_and_candidates_in_isolated_request(monkeypatch):
    req, plan = draft()
    plan.guides.sources = []
    calls = mocked_post(monkeypatch, response(missing=True))
    report, trace = actual_review_schedule(review_input(req, plan, CATALOG), runtime(), monotonic() + 60)
    assert report.status == "changes_requested" and trace["input_tokens"] == 123
    body = calls[0]["json"]
    task = json.loads(body["messages"][1]["content"])
    assert task["days"][0]["activities"][0]["end"] == "11:00"
    assert task["planning_context"]["day_windows"][0]["latest"] == "23:00"
    assert task["weather"]["days"][0]["status"] == "unknown"
    assert not {"tools", "guide_research", "messages", "llm_api_key"} & set(task)
    assert len(body["messages"]) == 2 and calls[0]["timeout"] <= 15


@pytest.mark.parametrize("invalid", ["missing_day", "duplicate_day", "invented_id", "wrong_day"])
def test_review_rejects_partial_days_unknown_references_and_contradictory_verdicts(monkeypatch, invalid):
    req, plan = draft()
    data = response(missing=True)
    if invalid == "missing_day":
        data["days"] = []
    elif invalid == "duplicate_day":
        data["days"].append(deepcopy(data["days"][0]))
    elif invalid == "invented_id":
        data["issues"][0]["alternative_ids"] = ["imagined-poi"]
    elif invalid == "wrong_day":
        data["issues"][0]["place_ids"] = ["wulin"]  # Known candidate, not this day's selected place.
    mocked_post(monkeypatch, data)
    report, _ = actual_review_schedule(review_input(req, plan, CATALOG), runtime(), monotonic() + 60)
    assert report.status == "unavailable" and report.failure_code == "invalid_output"


def test_daily_missing_and_pace_labels_remain_usable_without_redundant_category_rows(monkeypatch):
    req, plan = draft()
    data = response(missing=True)
    data["days"][0]["pace"] = "too_sparse"
    data["issues"] = []
    mocked_post(monkeypatch, data)
    report, _ = actual_review_schedule(review_input(req, plan, CATALOG), runtime(), monotonic() + 60)
    assert report.status == "changes_requested"
    assert {i.category for i in report.issues} == {"evening", "pace"}
    assert all(i.evidence == data["days"][0]["explanation"] for i in report.issues)


def test_review_failure_is_sanitized_and_budget_is_reserved(monkeypatch):
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("SECRET_PROVIDER_PAYLOAD")

    monkeypatch.setattr("app.review_agent.httpx.post", fail)
    req, plan = draft()
    report, _ = actual_review_schedule(review_input(req, plan, CATALOG), runtime(), monotonic() + 60)
    assert report.failure_code == "timeout" and "SECRET" not in report.model_dump_json()
    report, _ = actual_review_schedule(
        review_input(req, plan, CATALOG), runtime(), monotonic() + 20, reserve_seconds=25
    )
    assert report.failure_code == "budget_exhausted" and len(calls) == 1


def test_successful_review_replaces_ratio_and_gap_verdicts_but_cannot_waive_hard_conflicts(monkeypatch):
    req, plan = draft()
    assert not any(c.code in {"schedule_long_gap", "schedule_low_engagement"} for c in plan.checks)
    fake_reviews(monkeypatch, [response()])
    audit_schedule(req, plan, CATALOG, TravelTools(runtime()))
    checked = validate_itinerary(req, plan)
    assert current_review(req, checked, CATALOG)
    assert not any(c.code in {"schedule_long_gap", "schedule_low_engagement"} for c in checked.checks)
    # An unreasonably late activity is still forbidden even with an explicit model pass.
    plan.days[0].activities[0].start, plan.days[0].activities[0].end = "22:30", "23:30"
    audit_schedule(req, plan, CATALOG, TravelTools(runtime()))
    checked = validate_itinerary(req, plan)
    assert any(c.code == "invalid_window" and c.severity == "error" for c in checked.checks)


def test_edit_invalidates_previous_review_and_restores_independent_signals(monkeypatch):
    req, plan = draft()
    fake_reviews(monkeypatch, [response()])
    audit_schedule(req, plan, CATALOG, TravelTools(runtime()))
    assert current_review(req, plan, CATALOG)
    plan.days[0].activities[0].end = "11:15"
    checked = validate_itinerary(req, plan)
    assert checked.planning_decision.quality_review.status == "not_requested"
    assert any(c.code == "quality_review_stale" for c in checked.checks)


def test_catalog_snapshot_can_contain_extra_candidates_without_invalidating_review(monkeypatch):
    req, plan = draft()
    fake_reviews(monkeypatch, [response()])
    audit_schedule(req, plan, CATALOG, TravelTools(runtime()))
    expanded = {
        **CATALOG,
        "extra": CATALOG["westlake"].model_copy(update={"id": "extra", "name": "额外备选"}),
    }
    checked = validate_itinerary(req, plan, catalog=expanded)
    assert current_review(req, checked, expanded)
    assert checked.planning_decision.quality_review.status == "passed"
    # Changes to a selected place's known hours still invalidate its review.
    expanded["gushan_museum"] = expanded["gushan_museum"].model_copy(update={"closing": "10:30"})
    checked = validate_itinerary(req, checked, catalog=expanded)
    assert checked.planning_decision.quality_review.status == "not_requested"
    assert any(c.code == "opening_hours" for c in checked.checks)


def test_natural_revision_reviews_each_draft_and_preserves_frozen_day(monkeypatch):
    from test_replanning import fixture, move_intent, selection, tools

    from app.replanning import run_revision
    from app.schemas import ScheduledDay

    req, base, catalog = fixture()
    base.planning_decision.source = "model_schedule"
    data = response(days=3, missing=True)
    for issue in data["issues"]:
        issue["alternative_ids"] = []
    reviews = fake_reviews(monkeypatch, [data, response(days=3)])
    replies = []

    def propose(*args):
        replies.append(args[2])
        return selection(
            daily_schedule=[
                ScheduledDay(**day([stop("p3", "09:00", "10:00"), stop("p5", "11:00", "12:00")], number=2)),
                ScheduledDay(**day([stop("p4", "09:00", "10:00"), stop("p2", "11:00", "12:00")], number=3)),
            ]
        )

    monkeypatch.setattr("app.replanning.model_json", propose)
    _, result, _, _ = run_revision(req, base, move_intent(), tools(), catalog, message="地点2放第三天")
    assert len(reviews) == 2 and len(replies) == 2
    assert any(f.get("code") == "reviewer_evening" for f in replies[1]["feedback"])
    assert result.days[0].model_dump() == base.days[0].model_dump()
    assert result.planning_decision.quality_review.status == "passed"
    assert not any(c.severity == "error" for c in result.checks)


def test_independent_reviewer_requests_evening_repair_even_without_night_interest(monkeypatch):
    first = [day([stop("gushan_museum", "10:00", "11:00")])]
    second = [day([stop("gushan_museum", "10:00", "11:00"), stop("wulin", "19:00", "20:00")])]
    main_calls = select_reply(monkeypatch, [first, second])
    reviews = fake_reviews(monkeypatch, [response(missing=True), response()])
    plan = generate(request(), runtime(), lambda *_: None)
    assert len(main_calls) == len(reviews) == 2
    feedback = main_calls[1]["feedback"]
    assert any(c["code"] == "reviewer_evening" for c in feedback)
    report = next(c["report"] for c in feedback if c["code"] == "reviewer_report")
    assert report["issues"][0]["alternative_ids"] == ["wulin"]
    assert plan.days[0].activities[-1].start == "19:00"
    assert plan.planning_decision.quality_review.status == "passed"
    assert not any(c.code.startswith("reviewer_") for c in plan.checks)


def test_no_unbounded_review_loop_and_unresolved_quality_is_visible(monkeypatch):
    main_calls = select_reply(monkeypatch, [[day([stop("gushan_museum", "10:00", "11:00")])]])
    reviews = fake_reviews(monkeypatch, [response(missing=True)])
    plan = generate(request(), runtime(), lambda *_: None)
    assert len(main_calls) == len(reviews) == 2
    assert plan.planning_decision.quality_review.status == "changes_requested"
    assert any(c.code == "reviewer_evening" and c.severity == "warning" for c in plan.checks)


def test_every_day_must_be_reviewed_even_when_another_day_has_night_activity(monkeypatch):
    schedule = [
        day([stop("wulin", "19:00", "20:00")]),
        day([stop("gushan_museum", "10:00", "11:00")], number=2),
    ]
    req, plan = draft(request(days=2), schedule)
    data = response(days=2)
    data["days"][1] = response(days=2, missing=True)["days"][1]
    data["issues"] = [response(days=2, missing=True)["issues"][1]]
    mocked_post(monkeypatch, data)
    report, _ = actual_review_schedule(review_input(req, plan, CATALOG), runtime(), monotonic() + 60)
    assert report.status == "changes_requested" and report.issues[0].day == 2


def test_reviewer_report_persists_with_task_and_confirmed_version(client, monkeypatch):
    select_reply(monkeypatch, [[day([stop("gushan_museum", "10:00", "11:00")])]])
    fake_reviews(monkeypatch, [response()])
    monkeypatch.setattr("app.main.request_settings", lambda *_: runtime())
    trip = client.post("/api/trips", json=request().model_dump(mode="json")).json()
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    preview = client.get(f"/api/runs/{run_id}").json()["proposal"]
    applied = client.post(
        f"/api/trips/{trip['id']}/proposals/{preview['id']}/apply",
        json={"base_version": 0, "idempotency_key": "review-persisted"},
    )
    assert applied.status_code == 200, applied.text
    saved = client.get(f"/api/trips/{trip['id']}").json()["itinerary"]
    assert saved["planning_decision"]["quality_review"]["status"] == "passed"
    with client.app.state.session_factory() as db:
        task = db.scalar(
            select(AgentTaskRecord).where(
                AgentTaskRecord.run_id == run_id, AgentTaskRecord.role == "reviewer"
            )
        )
        assert task.status == "completed" and task.result["days"][0]["day"] == 1
        assert "validation_signals" in task.input_snapshot and "llm_api_key" not in task.input_snapshot
