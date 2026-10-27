import json
from datetime import time
from urllib.parse import quote

import pytest

from app.agent import generate
from app.catalog import CATALOG
from app.config import Settings
from app.database import AgentTaskRecord, ProposalRecord, RunRecord, TripRecord, VersionRecord
from app.replanning import DaySelection, RepairPlan, run_revision, scoped_days
from app.revisions import CandidateSearch, EditOperation, RevisionIntent, revision_context
from app.schemas import Requirements, TravelBoundary
from app.services import legacy_place_days
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def fixture():
    req = Requirements(start_date="2026-10-24", days=3, day_end="12:20", lodging_mode="route_first")
    catalog = {
        f"p{i}": CATALOG["westlake"].model_copy(
            update={
                "id": f"p{i}",
                "name": f"地点{i}",
                "longitude": 120.16 + i * 0.001,
                "latitude": 30.25,
                "duration_minutes": 60,
                "opening": "09:00",
                "closing": "18:00",
            }
        )
        for i in range(6)
    }
    base = generate(req, Settings(_env_file=None), lambda *_: None, catalog=catalog)
    assert [[a.place_id for a in d.activities] for d in base.days] == [
        ["p0", "p1"],
        ["p2", "p3"],
        ["p4", "p5"],
    ]
    return req, base, catalog


def tools():
    return TravelTools(Settings(planning_mode="llm", _env_file=None))


def move_intent(**changes):
    return RevisionIntent(
        summary="地点2放到第三天",
        operations=[EditOperation(kind="move", day=2, target_day=3, place_id="p2")],
        **changes,
    )


def selection(two=("p3", "p5"), three=("p4", "p2"), **changes):
    return RepairPlan(
        summary="把第三天普通活动移到第二天，为用户指定地点腾出时间。",
        days=[DaySelection(day=2, place_ids=list(two)), DaySelection(day=3, place_ids=list(three))],
        **changes,
    )


def test_conflict_is_sent_back_to_model_and_other_stop_moves_to_free_day(monkeypatch):
    req, base, catalog = fixture()
    received = []

    def model(*args):
        received.append(args[2])
        return selection()

    monkeypatch.setattr("app.replanning.model_json", model)
    updated, result, _, audit = run_revision(
        req, base, move_intent(), tools(), catalog, message="把地点2移到第三天"
    )
    assert len(received) == 1 and received[0]["feedback"]
    assert received[0]["allowed_days"] == [2, 3]
    assert result.days[0] == base.days[0]
    assert [[a.place_id for a in d.activities] for d in result.days[1:]] == [["p3", "p5"], ["p4", "p2"]]
    assert updated.place_days == {"p2": 3} and req.place_days == {}
    assert not any(c.severity == "error" for c in result.checks)
    assert result.revision_rounds == 1 and audit["rounds"][1]["stage"] == "planned"
    assert any("地点5" in line and "第 2 天" in line for line in result.revision_changes)


def test_model_cannot_move_a_previously_user_assigned_place_to_free_space(monkeypatch):
    req, base, catalog = fixture()
    req.place_days = {"p4": 3}
    replies = iter([selection(two=("p3", "p4"), three=("p5", "p2")), selection()])
    monkeypatch.setattr("app.replanning.model_json", lambda *_: next(replies))
    updated, result, _, audit = run_revision(
        req, base, move_intent(), tools(), catalog, message="把地点2移到第三天"
    )
    assert updated.place_days == {"p4": 3, "p2": 3}
    assert result.revision_rounds == 2 and audit["rounds"][1]["stage"] == "rejected"
    assert not any(c.severity == "error" for c in result.checks)


def test_three_repair_attempts_are_bounded_and_never_drop_required_stops(monkeypatch):
    req, base, catalog = fixture()
    calls = []

    def wrong(*_):
        calls.append(1)
        return selection(two=("p3",), three=("p4", "p2"))

    monkeypatch.setattr("app.replanning.model_json", wrong)
    _, result, _, audit = run_revision(
        req, base, move_intent(), tools(), catalog, message="把地点2移到第三天"
    )
    assert len(calls) == 3 and audit["stop_reason"] == "round_limit"
    assert audit["fallback"] == "program_verified" and result.revision_status == "unavailable"
    assert not any(c.severity == "error" for c in result.checks)
    assert {a.place_id for day in result.days for a in day.activities} == set(catalog)


def test_replacement_permission_keeps_a_feasible_precheck_when_model_never_fits_the_day(monkeypatch):
    req, base, catalog = fixture()

    def crowded(*_):
        return selection(two=("p3",), three=("p4", "p5", "p2"))

    monkeypatch.setattr("app.replanning.model_json", crowded)
    updated, result, _, audit = run_revision(
        req,
        base,
        move_intent(reselect=True, allow_drop_optional=True),
        tools(),
        catalog,
        message="把地点2移到第三天",
    )
    assert audit["fallback"] == "program_verified"
    assert not any(c.severity == "error" for c in result.checks)
    assert not any(c.code == "reselection_incomplete" for c in result.checks)
    assert updated.place_days == {"p2": 3} and result.days[0] == base.days[0]
    assert {a.place_id for day in result.days for a in day.activities} == set(catalog)


def test_permission_alone_does_not_force_another_model_request_for_a_valid_change(monkeypatch):
    req, base, catalog = fixture()

    def unexpected(*_):
        raise AssertionError("Permission to replace is not a request to replace")

    monkeypatch.setattr("app.replanning.model_json", unexpected)
    intent = RevisionIntent(
        summary="第二天晚出发20分钟",
        reselect=True,
        allow_drop_optional=True,
        operations=[EditOperation(kind="delay", day=2, minutes=20)],
    )
    _, result, _, audit = run_revision(req, base, intent, tools(), catalog, message="第二天晚出发20分钟")
    assert not any(c.severity == "error" for c in result.checks)
    assert len(audit["rounds"]) == 1 and result.revision_rounds == 0


def test_explicit_reselection_is_still_required_even_when_a_time_change_fits(monkeypatch):
    req, base, catalog = fixture()
    monkeypatch.setattr("app.replanning.model_json", lambda *_: RepairPlan(summary="无效分组", days=[]))
    intent = RevisionIntent(
        summary="晚出发并重新挑景点",
        reselect=True,
        require_reselection=True,
        allow_drop_optional=True,
        operations=[EditOperation(kind="delay", day=2, minutes=20)],
    )
    _, result, _, audit = run_revision(
        req, base, intent, tools(), catalog, message="第二天晚出发20分钟，并重新挑选景点"
    )
    assert len(audit["rounds"]) == 4 and "fallback" not in audit
    assert any(c.code == "reselection_incomplete" for c in result.checks)


def test_a_full_snapshot_can_repeat_the_unchanged_frozen_day(monkeypatch):
    req, base, catalog = fixture()
    proposal = selection()
    proposal.days.insert(0, DaySelection(day=1, place_ids=["p0", "p1"]))
    monkeypatch.setattr("app.replanning.model_json", lambda *_: proposal)
    _, result, _, audit = run_revision(
        req,
        base,
        move_intent(reselect=True, allow_drop_optional=True),
        tools(),
        catalog,
        message="把地点2移到第三天",
    )
    assert result.revision_rounds == 1 and result.days[0] == base.days[0]
    assert not any(c.severity == "error" for c in result.checks)
    assert audit["rounds"][1]["scope_handling"]["unchanged_frozen_days"] == [1]


def test_redundant_frozen_place_ids_are_retained_only_on_their_original_day(monkeypatch):
    req, base, catalog = fixture()
    monkeypatch.setattr("app.replanning.model_json", lambda *_: selection(three=("p0", "p4", "p2")))
    _, result, _, audit = run_revision(
        req, base, move_intent(), tools(), catalog, message="把地点2移到第三天"
    )
    assert result.days[0] == base.days[0] and result.revision_rounds == 1
    assert audit["rounds"][1]["scope_handling"]["retained_frozen_places"] == ["p0"]
    assert [a.place_id for a in result.days[2].activities] == ["p4", "p2"]


def test_changing_a_frozen_day_is_rejected_and_the_proposal_is_audited(monkeypatch):
    req, base, catalog = fixture()
    invalid = selection()
    invalid.days.insert(0, DaySelection(day=1, place_ids=["p1", "p0"]))
    answers = iter([invalid, selection()])
    monkeypatch.setattr("app.replanning.model_json", lambda *_: next(answers))
    _, result, _, audit = run_revision(
        req, base, move_intent(), tools(), catalog, message="把地点2移到第三天"
    )
    assert result.days[0] == base.days[0] and result.revision_rounds == 2
    rejected = audit["rounds"][1]
    assert rejected["stage"] == "rejected" and rejected["proposal"]["days"][0]["place_ids"] == ["p1", "p0"]


def test_exhausted_repairs_without_a_feasible_precheck_remain_blocked(monkeypatch):
    req, base, catalog = fixture()
    req.place_days = {"p4": 3, "p5": 3}
    monkeypatch.setattr(
        "app.replanning.model_json", lambda *_: selection(two=("p3",), three=("p4", "p5", "p2"))
    )
    _, result, _, audit = run_revision(
        req, base, move_intent(), tools(), catalog, message="把地点2移到第三天"
    )
    assert "fallback" not in audit and result.revision_rounds == 3
    assert any(c.severity == "error" for c in result.checks)


def test_local_only_option_reports_conflict_without_changing_other_activities(monkeypatch):
    req, base, catalog = fixture()

    def unexpected(*_):
        raise AssertionError("No flexible repair in local-only mode")

    monkeypatch.setattr("app.replanning.model_json", unexpected)
    _, result, _, audit = run_revision(
        req, base, move_intent(scope="local"), tools(), catalog, message="把地点2移到第三天，其他活动不变"
    )
    assert len(audit["rounds"]) == 1 and any(c.severity == "error" for c in result.checks)
    assert result.days[0] == base.days[0]


def test_frozen_day_cannot_also_be_a_move_source():
    req, base, catalog = fixture()
    with pytest.raises(ToolUnavailable, match="同时"):
        run_revision(
            req, base, move_intent(frozen_days=[2]), tools(), catalog, message="第二天不变，把地点2移到第三天"
        )


def test_repair_rejects_unknown_ids_and_keeps_the_catalog_evidence(monkeypatch):
    req, base, catalog = fixture()
    calls = []

    def model(*args):
        calls.append(args[2])
        return selection(two=("p3", "fake-place")) if len(calls) == 1 else selection()

    monkeypatch.setattr("app.replanning.model_json", model)
    _, result, new_catalog, audit = run_revision(
        req, base, move_intent(), tools(), catalog, message="把地点2移到第三天"
    )
    assert "fake-place" not in new_catalog and audit["rounds"][1]["stage"] == "rejected"
    assert result.revision_rounds == 2 and not any(c.severity == "error" for c in result.checks)


def test_demo_cross_day_coordination_uses_bounded_rules_and_is_labeled():
    req, base, catalog = fixture()
    _, result, _, _ = run_revision(
        req, base, move_intent(), TravelTools(Settings(_env_file=None)), catalog, message="把地点2移到第三天"
    )
    assert result.revision_status == "rules_only" and "演示规则" in result.revision_note
    assert not any(c.severity == "error" for c in result.checks)


def test_named_unknown_place_is_searched_and_pinned_before_reselection(monkeypatch):
    req, base, catalog = fixture()
    candidate = catalog["p5"].model_copy(
        update={
            "id": "amap-new",
            "name": "新公园",
            "fixture": False,
            "source_url": "https://uri.amap.com/marker?position=120.16,30.25",
            "evidence_note": "高德查询测试快照",
        }
    )
    runtime = tools()
    queries = []
    monkeypatch.setattr(
        runtime,
        "search_candidates",
        lambda query, category, anchor: queries.append(query) or {candidate.id: candidate},
    )
    intent = RevisionIntent(
        summary="第三天去新公园，普通景点可替换",
        scope="all_days",
        reselect=True,
        allow_drop_optional=True,
        searches=[CandidateSearch(query="新公园", day=3, category="nature")],
        operations=[EditOperation(kind="add", day=3, place_query="新公园")],
    )
    monkeypatch.setattr(
        "app.replanning.model_json",
        lambda *_: RepairPlan(
            summary="替换普通推荐，为新公园留出时间。",
            days=[
                DaySelection(day=1, place_ids=["p0", "p1"]),
                DaySelection(day=2, place_ids=["p2", "p3"]),
                DaySelection(day=3, place_ids=["p4", "amap-new"]),
            ],
        ),
    )
    updated, result, new_catalog, audit = run_revision(
        req, base, intent, runtime, catalog, message="第三天去新公园，其他普通景点可以替换"
    )
    assert queries == ["新公园"] and "amap-new" not in catalog
    assert updated.place_days["amap-new"] == 3 and not new_catalog["amap-new"].fixture
    assert not any(c.severity == "error" for c in result.checks)
    assert any("新增 新公园" in change for change in result.revision_changes)
    assert len(audit["searches"]) == 1


def test_ambiguous_named_search_never_selects_a_branch_arbitrarily(monkeypatch):
    req, base, catalog = fixture()
    runtime = tools()
    pool = {
        f"amap-{i}": catalog["p5"].model_copy(update={"id": f"amap-{i}", "name": f"新公园（{i}店）"})
        for i in range(2)
    }
    monkeypatch.setattr(runtime, "search_candidates", lambda *_: pool)
    intent = RevisionIntent(
        summary="新增公园", operations=[EditOperation(kind="add", day=3, place_query="新公园")]
    )
    with pytest.raises(ToolUnavailable, match="唯一匹配"):
        run_revision(req, base, intent, runtime, catalog, message="第三天去新公园")


def test_search_budget_and_invalid_reselection_cannot_be_saved_as_success(monkeypatch):
    req, base, catalog = fixture()
    runtime = tools()
    requests = []
    monkeypatch.setattr(runtime, "search_candidates", lambda query, *_: requests.append(query) or {})
    monkeypatch.setattr(
        "app.replanning.model_json",
        lambda *_: RepairPlan(summary="继续查询", searches=[CandidateSearch(query="公园三", day=3)]),
    )
    intent = RevisionIntent(
        summary="重新选景点",
        scope="all_days",
        reselect=True,
        allow_drop_optional=True,
        searches=[CandidateSearch(query="公园一", day=3), CandidateSearch(query="公园二", day=2)],
    )
    _, result, _, audit = run_revision(req, base, intent, runtime, catalog, message="重新选景点")
    assert requests == ["公园一", "公园二"] and len(audit["rounds"]) == 4
    assert any(c.code == "reselection_incomplete" for c in result.checks)
    assert result.status == "NO_SOLUTION_WITHIN_LIMIT"


def test_incremental_search_filters_bad_coordinates_and_wrong_city(monkeypatch):
    runtime = TravelTools(Settings(tool_provider="amap", _env_file=None), "杭州")
    requests = []

    def get(path, params):
        requests.append((path, params))
        return {
            "pois": [
                {"id": "ok", "name": "公园", "location": "120.16,30.25", "cityname": "杭州市"},
                {"id": "bad", "name": "坏数据", "location": "999,999"},
                {"id": "other", "name": "异地", "location": "120.1,30.2", "cityname": "成都市"},
                {"id": "station", "name": "公园站", "location": "120.16,30.25", "typecode": "150500"},
                {"id": "address", "name": "公园", "location": "120.16,30.25", "typecode": "190700"},
            ]
        }

    monkeypatch.setattr(runtime, "_get", get)
    result = runtime.search_candidates("公园", "nature", CATALOG["westlake"])
    assert set(result) == {"amap-ok"} and requests[0][0] == "/v3/place/around"
    assert requests[0][1]["offset"] == 5 and requests[0][1]["citylimit"] == "true"
    assert result["amap-ok"].opening is None and "尚未核验" in result["amap-ok"].evidence_note


def test_catalog_is_preview_scoped_and_follows_confirm_restore(client, monkeypatch):
    trip = client.post(
        "/api/trips", json={"start_date": "2026-10-24", "days": 3, "lodging_mode": "route_first"}
    ).json()
    trip_id = trip["id"]
    run_id = client.post(f"/api/trips/{trip_id}/plan-runs").json()["run_id"]
    original = client.get(f"/api/runs/{run_id}").json()["proposal"]

    def confirm(proposal):
        response = client.post(
            f"/api/trips/{trip_id}/proposals/{proposal['id']}/apply",
            json={"base_version": proposal["base_version"], "idempotency_key": "save-" + proposal["id"]},
        )
        assert response.status_code == 200, response.text

    confirm(original)
    candidate = CATALOG["westlake"].model_copy(
        update={"id": "amap-new-park", "name": "新公园", "fixture": False, "duration_minutes": 60}
    )
    intent = RevisionIntent(
        summary="重新挑选并增加新公园",
        scope="all_days",
        reselect=True,
        allow_drop_optional=True,
        operations=[EditOperation(kind="add", day=3, place_query="新公园")],
    )
    monkeypatch.setattr("app.services.interpret_revision", lambda *_: intent)
    monkeypatch.setattr(TravelTools, "search_candidates", lambda *_: {candidate.id: candidate})
    days = [
        DaySelection(day=d["day"], place_ids=[a["place_id"] for a in d["activities"]])
        for d in original["itinerary"]["days"]
    ]
    days[-1].place_ids[-1] = candidate.id
    monkeypatch.setattr(
        "app.replanning.model_json", lambda *_: RepairPlan(summary="用新公园替换普通推荐。", days=days)
    )
    headers = {
        "X-Travel-Config": quote(
            json.dumps(
                {
                    "tool_provider": "demo",
                    "planning_mode": "llm",
                    "llm_base_url": "https://model.example/v1",
                    "llm_model": "fixture",
                    "llm_api_key": "fake-key",
                }
            )
        )
    }
    started = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"base_version": 1, "message": "第三天增加新公园，普通推荐可替换"},
        headers=headers,
    )
    revised = client.get(f"/api/runs/{started.json()['run_id']}").json()
    assert revised["status"] == "completed", revised
    proposal = revised["proposal"]
    assert candidate.id in {p["id"] for p in proposal["places"]}
    assert candidate.id not in {p["id"] for p in client.get(f"/api/trips/{trip_id}").json()["places"]}
    confirm(proposal)
    saved = client.get(f"/api/trips/{trip_id}").json()
    assert (
        candidate.id in {p["id"] for p in saved["places"]}
        and saved["requirements"]["place_days"][candidate.id] == 3
    )
    for version, base_version in [(1, 2), (2, 3)]:
        restored = client.post(
            f"/api/trips/{trip_id}/versions/{version}/restore",
            json={"base_version": base_version, "idempotency_key": f"restore-catalog-{version}"},
        )
        assert restored.status_code == 200, restored.text
        actual = client.get(f"/api/trips/{trip_id}").json()
        assert (candidate.id in {p["id"] for p in actual["places"]}) == (version == 2)


def test_place_day_assignment_has_an_independent_validation_check():
    req, base, catalog = fixture()
    req.place_days = {"p2": 3}
    result = validate_itinerary(req, base, catalog=catalog)
    assert any(c.code == "place_day_changed" for c in result.checks)


def test_confirmed_legacy_moves_are_recovered_only_if_still_applicable(client):
    req, base, catalog = fixture()
    factory = client.app.state.session_factory
    with factory.begin() as db:
        trip = TripRecord(
            requirements=req.model_dump(mode="json"),
            catalog={pid: p.model_dump() for pid, p in catalog.items()},
            current_version=1,
        )
        db.add(trip)
        db.flush()
        proposal = ProposalRecord(
            trip_id=trip.id,
            base_version=0,
            itinerary=base.model_dump(mode="json"),
            label="old",
            applied_version=1,
        )
        db.add(proposal)
        db.flush()
        run = RunRecord(trip_id=trip.id, base_version=0, status="completed", proposal_id=proposal.id)
        db.add(run)
        db.flush()
        db.add(
            AgentTaskRecord(
                run_id=run.id,
                role="main",
                input_snapshot={},
                result={
                    "intent": {
                        "operations": [
                            {"kind": "move", "place_id": "p4", "target_day": 3},
                            {"kind": "move", "place_id": "p2", "target_day": 3},
                        ]
                    }
                },
            )
        )
        trip_id = trip.id
    with factory.begin() as db:
        assert legacy_place_days(db, trip_id, 1, base.model_dump(mode="json")) == {"p4": 3}
        db.add(
            VersionRecord(
                trip_id=trip_id, number=2, label="从版本 1 恢复", itinerary=base.model_dump(mode="json")
            )
        )
    with factory() as db:
        assert legacy_place_days(db, trip_id, 2, base.model_dump(mode="json")) == {}


def test_generic_new_search_cannot_be_satisfied_by_an_already_scheduled_place(monkeypatch):
    req, base, catalog = fixture()
    runtime = tools()
    monkeypatch.setattr(runtime, "search_candidates", lambda *_: {"p4": catalog["p4"]})
    intent = RevisionIntent(
        summary="增加一个新景点", searches=[CandidateSearch(query="公园", day=3, required=True)]
    )
    with pytest.raises(ToolUnavailable, match="不能完成"):
        run_revision(req, base, intent, runtime, catalog, message="第三天增加一个新公园")


def test_failed_searches_also_consume_the_two_query_budget(monkeypatch):
    req, base, catalog = fixture()
    runtime = tools()
    calls = []
    queries = iter(["景点一", "景点二", "景点三"])

    def unavailable(query, *_):
        calls.append(query)
        raise ToolUnavailable("地图服务暂不可用")

    monkeypatch.setattr(runtime, "search_candidates", unavailable)
    monkeypatch.setattr(
        "app.replanning.model_json",
        lambda *_: RepairPlan(summary="补查", searches=[CandidateSearch(query=next(queries), day=3)]),
    )
    _, result, _, audit = run_revision(
        req,
        base,
        RevisionIntent(summary="重新选景点", scope="all_days", reselect=True, allow_drop_optional=True),
        runtime,
        catalog,
        message="重新选景点",
    )
    assert calls == ["景点一", "景点二"]
    assert all(record["status"] == "unavailable" for record in audit["searches"])
    assert any(c.severity == "error" for c in result.checks)


def test_replanning_context_bounds_optional_catalog_and_includes_latest_searches():
    req, base, catalog = fixture()
    catalog.update(
        {f"extra-{i}": CATALOG["westlake"].model_copy(update={"id": f"extra-{i}"}) for i in range(100)}
    )
    context = revision_context(req, base, catalog)
    assert len(context["catalog"]) == 30 and context["catalog_total"] == 106
    assert "extra-99" in {row["id"] for row in context["catalog"]}
    assert {a.place_id for day in base.days for a in day.activities} <= {
        row["id"] for row in context["catalog"]
    }


def test_new_activity_on_third_day_can_coordinate_with_second_day():
    req, _, _ = fixture()
    intent = RevisionIntent(
        summary="第三天新增", operations=[EditOperation(kind="add", day=3, place_id="new")]
    )
    assert scoped_days(req, intent) == {2, 3}
    intent.frozen_days = [2]
    assert scoped_days(req, intent) == {3}


def test_early_departure_can_move_all_optional_last_day_stops_to_previous_day(monkeypatch):
    req, _, catalog = fixture()
    req.day_end = time(18)
    req.departure = TravelBoundary(
        location={"name": "机场", "longitude": 120.16, "latitude": 30.25}, time="18:00", buffer_minutes=60
    )
    base = generate(req, Settings(_env_file=None), lambda *_: None, catalog=catalog)
    first = [a.place_id for a in base.days[0].activities]
    remaining = [a.place_id for day in base.days[1:] for a in day.activities]
    monkeypatch.setattr(
        "app.replanning.model_json",
        lambda *_: RepairPlan(
            summary="提前返程，末日仅接驳，其余活动挪到第二天。",
            days=[DaySelection(day=2, place_ids=remaining), DaySelection(day=3, place_ids=[])],
        ),
    )
    updated, result, _, _ = run_revision(
        req,
        base,
        RevisionIntent(summary="最后一天早上返程", operations=[EditOperation(kind="departure", at="09:00")]),
        tools(),
        catalog,
        message="返程改为09:00，其他普通活动可以协调",
    )
    assert updated.departure.time.hour == 9 and not result.days[2].activities
    assert not any(c.severity == "error" for c in result.checks)
    assert {a.place_id for day in result.days for a in day.activities} == set(catalog)
    assert [a.place_id for a in result.days[0].activities] == first
