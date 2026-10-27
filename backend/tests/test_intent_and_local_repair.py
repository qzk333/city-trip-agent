import json

from test_candidate_quality import poi
from test_model_planning_decisions import model_reply, runtime
from test_timed_weather_planning import day, forecast, select_reply, stop

from app.agent import generate
from app.catalog import CATALOG
from app.llm import model_candidates
from app.place_evidence import shelter_assessment
from app.planner import build_plan
from app.schemas import PlaceRecommendation, PlanningDecision, Requirements, TravelProfile, VenueAssessment
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def test_lunch_overlap_repaired_without_moving_activity_for_night_prose():
    catalog = {
        p.id: p
        for p in [
            poi("park", "莲花山公园", "nature"),
            poi("museum", "深圳博物馆新馆"),
            poi("bridge", "深圳湾观海栈桥", "nature"),
        ]
    }
    req = Requirements(
        start_date="2026-10-27",
        day_end="23:00",
        lodging_mode="route_first",
        profile=TravelProfile(themes=["night"]),
    )
    schedule = [
        day(
            [
                stop("amap-park", "09:30", "11:15"),
                stop("amap-museum", "11:47", "13:02"),
                {**stop("amap-bridge", "16:18", "17:33"), "reason": "落实夜间观景需求"},
            ],
            breaks=[
                {"start": "12:00", "end": "13:00", "kind": "meal", "reason": "午餐，与参观分开"},
                {"start": "13:02", "end": "14:27", "kind": "rest", "reason": "午后休息"},
                {"start": "17:33", "end": "18:30", "kind": "meal", "reason": "晚餐"},
            ],
        )
    ]
    tools = TravelTools(runtime())
    options = {"model_schedule": schedule}
    plan = build_plan(req, list(catalog), tools, options, catalog)
    plan.planning_decision = PlanningDecision(source="model_schedule")
    plan = validate_itinerary(req, plan, options, catalog)
    assert plan.days[0].activities[-1].start == "16:18"
    lunch = next(b for b in plan.days[0].breaks if b.kind == "meal" and b.start < "16:00")
    assert lunch.start >= "13:02"
    assert not any(c.severity == "error" for c in plan.checks)
    assert not any(c.code in {"recommendation_time_mismatch", "schedule_night_missing"} for c in plan.checks)
    assert any(c.code == "break_adjusted" for c in plan.checks)


def test_fixed_afternoon_time_is_preserved_for_whole_plan_review():
    p = poi("bridge", "海上栈桥", "nature")
    req = Requirements(
        start_date="2026-10-27",
        day_end="23:00",
        lodging_mode="route_first",
        profile=TravelProfile(themes=["night"]),
        appointments=[{"place_id": p.id, "day": 1, "start": "16:18", "end": "17:33"}],
    )
    options = {"model_schedule": [day([{**stop(p.id, "16:18", "17:33"), "time_intent": "night"}])]}
    tools = TravelTools(runtime())
    plan = build_plan(req, [p.id], tools, options, {p.id: p})
    plan.planning_decision = PlanningDecision(source="model_schedule")
    plan = validate_itinerary(req, plan, options, {p.id: p})
    assert plan.days[0].activities[0].start == "16:18"
    assert not any(c.code in {"recommendation_time_mismatch", "schedule_night_missing"} for c in plan.checks)


def test_known_closing_hours_prevent_automatic_night_move():
    p = poi("bridge", "栈桥", "nature").model_copy(update={"closing": "18:00"})
    req = Requirements(start_date="2026-10-27", day_end="23:00", lodging_mode="route_first")
    options = {"model_schedule": [day([{**stop(p.id, "16:00", "17:00"), "time_intent": "night"}])]}
    plan = build_plan(req, [p.id], TravelTools(runtime()), options, {p.id: p})
    plan.planning_decision = PlanningDecision(source="model_schedule")
    plan = validate_itinerary(req, plan, options, {p.id: p})
    assert plan.days[0].activities[0].start == "16:00"
    assert not any(c.code == "recommendation_time_mismatch" for c in plan.checks)


def test_model_suggested_window_does_not_override_its_valid_actual_time():
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    options = {
        "model_schedule": [
            day(
                [
                    {
                        **stop("gushan_museum", "10:00", "11:00"),
                        "earliest_start": "14:00",
                        "latest_end": "16:00",
                    }
                ]
            )
        ]
    }
    plan = build_plan(req, list(CATALOG), TravelTools(runtime()), options)
    assert plan.days[0].activities[0].start == "10:00"
    assert plan.days[0].activities[0].end == "11:00"


def test_no_free_meal_window_keeps_real_conflict_and_names_overlapping_activity():
    p = poi("museum", "全日展馆")
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    options = {
        "model_schedule": [
            day(
                [stop(p.id, "09:00", "18:00")],
                breaks=[{"start": "12:00", "end": "13:00", "kind": "meal", "reason": "午餐"}],
            )
        ]
    }
    plan = validate_itinerary(
        req, build_plan(req, [p.id], TravelTools(runtime()), options, {p.id: p}), options, {p.id: p}
    )
    conflict = next(c for c in plan.checks if c.code == "break_overlap")
    assert "全日展馆" in conflict.message and "60 分钟" in conflict.message
    assert any(b.kind == "meal" for b in plan.days[0].breaks)


def test_model_venue_assessment_is_validated_without_mutating_map_fact(monkeypatch):
    p = poi("bridge", "海上栈桥", "nature")
    model_reply(
        monkeypatch,
        {
            "daily_schedule": [day([stop(p.id, "19:00", "20:00")])],
            "place_assessments": [
                {
                    "place_id": p.id,
                    "indoor_outdoor": "outdoor",
                    "confidence": "high",
                    "reason": "名称和地图分类指向露天海上步道",
                },
                {
                    "place_id": "invented",
                    "indoor_outdoor": "indoor",
                    "confidence": "high",
                    "reason": "无效地点",
                },
            ],
        },
    )
    output = {}
    model_candidates(
        Requirements(start_date="2026-10-27"),
        runtime(),
        [],
        lambda *_: None,
        catalog={p.id: p},
        recommendations=output,
    )
    assert len(output["place_assessments"]) == 1
    assert output["place_assessments"][0].basis == "model"
    assert p.traits.indoor_outdoor == "unknown"


def test_rain_plan_uses_model_indoor_judgment_and_retains_inference_label():
    p = poi("museum", "历史博物馆")
    req = Requirements(start_date="2026-10-27", lodging_mode="route_first")
    options = {
        "model_schedule": [day([stop(p.id, "10:00", "11:00")])],
        "weather_snapshot": forecast().model_dump(mode="json"),
    }
    plan = build_plan(req, [p.id], TravelTools(runtime()), options, {p.id: p})
    assessment = VenueAssessment(
        place_id=p.id, indoor_outdoor="indoor", confidence="high", reason="以室内展厅为主，附属广场另计"
    )
    plan.planning_decision = PlanningDecision(source="model_schedule", place_assessments=[assessment])
    assert shelter_assessment(p, plan.planning_decision.place_assessments) == ("indoor", "model")
    plan.planning_decision.place_assessments = [assessment.model_copy(update={"indoor_outdoor": "outdoor"})]
    assert shelter_assessment(p, plan.planning_decision.place_assessments) == ("outdoor", "model")
    plan.planning_decision.place_assessments = [assessment.model_copy(update={"confidence": "low"})]
    assert shelter_assessment(p, plan.planning_decision.place_assessments)[0] == "unknown"


def test_review_failure_has_safe_structured_cause_and_never_echoes_private_exception(monkeypatch):
    from test_review_agent import fake_reviews, response

    fake_reviews(monkeypatch, [response(missing=True)])
    select_reply(
        monkeypatch,
        [
            [day([stop("gushan_museum", "10:00", "11:00")])],
            ToolUnavailable("private-key-in-provider-error", code="timeout"),
        ],
    )
    plan = generate(
        Requirements(start_date="2026-10-27", day_end="23:00", pace="balanced", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
    )
    assert plan.planning_decision.review_failure.code == "timeout"
    assert plan.planning_decision.review_failure.retryable
    assert "private-key" not in json.dumps(plan.model_dump(mode="json"))


def test_legacy_night_prose_does_not_rewrite_concrete_schedule(monkeypatch):
    p = poi("bridge", "海上栈桥", "nature")

    def select(req, settings, trace, emit, deadline, catalog, guides, output, **kwargs):
        output["daily_schedule"] = [day([stop(p.id, "16:18", "17:33")])]
        output["day_groups"] = [[p.id]]
        output["place_reasons"] = [
            PlaceRecommendation(place_id=p.id, reason="适合傍晚至入夜看海景，落实夜间观景需求")
        ]
        output["place_assessments"] = [
            VenueAssessment(
                place_id=p.id, indoor_outdoor="outdoor", confidence="high", reason="海上步道通常露天"
            )
        ]
        return [p.id]

    monkeypatch.setattr("app.agent.model_candidates", select)
    plan = generate(
        Requirements(start_date="2026-10-27", day_end="23:00", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
        catalog={p.id: p},
    )
    assert plan.days[0].activities[0].start == "16:18"
    assert plan.planning_decision.place_assessments[0].indoor_outdoor == "outdoor"


def test_model_environment_judgment_persists_without_overwriting_poi_facts(client, monkeypatch):
    from app.database import CatalogRecord

    p = poi("museum", "历史博物馆")
    with client.app.state.session_factory.begin() as db:
        snapshot = CatalogRecord(city_id=19598, places={p.id: p.model_dump()})
        db.add(snapshot)
        db.flush()
        catalog_id = snapshot.id

    def select(req, settings, trace, emit, deadline, catalog, guides, output, **kwargs):
        output["daily_schedule"] = [day([stop(p.id, "10:00", "11:00")])]
        output["day_groups"] = [[p.id]]
        output["place_assessments"] = [
            VenueAssessment(
                place_id=p.id,
                indoor_outdoor="indoor",
                confidence="medium",
                reason="展厅通常为室内，入口和附属空间另计",
            )
        ]
        return [p.id]

    monkeypatch.setattr("app.agent.model_candidates", select)
    monkeypatch.setattr("app.main.request_settings", lambda *_: runtime())
    trip = client.post(
        "/api/trips",
        json={"start_date": "2026-10-27", "catalog_id": catalog_id, "lodging_mode": "route_first"},
    ).json()
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    proposal = client.get(f"/api/runs/{run_id}").json()["proposal"]
    saved = client.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/apply",
        json={"base_version": 0, "idempotency_key": "venue-persist-check"},
    )
    assert saved.status_code == 200, saved.text
    result = client.get(f"/api/trips/{trip['id']}").json()
    assert result["itinerary"]["planning_decision"]["place_assessments"][0]["basis"] == "model"
    assert result["itinerary"]["planning_decision"]["place_assessments"][0]["confidence"] == "medium"
    assert result["places"][0]["traits"]["indoor_outdoor"] == "unknown"
