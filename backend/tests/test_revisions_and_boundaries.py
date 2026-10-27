import json

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.agent import generate
from app.catalog import CATALOG
from app.config import ROOT, Settings
from app.hotel_routes import select_hotel
from app.lodging import boundary_window
from app.planner import minutes
from app.revisions import (
    EditOperation,
    HotelReview,
    RevisionIntent,
    interpret_revision,
    review_hotel_choice,
    revise_route,
)
from app.schemas import Activity, HotelChoiceRequest, Requirements, RouteLeg
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def req(**changes):
    return Requirements(start_date="2026-10-24", days=3, lodging_mode="route_first", **changes)


def settings():
    return Settings(_env_file=None)


def draft(requirements=None):
    return generate(requirements or req(), settings(), lambda *_: None)


def boundary(name, at, buffer):
    return {
        "location": {"name": name, "longitude": 120.163, "latitude": 30.251},
        "time": at,
        "buffer_minutes": buffer,
    }


def save_draft(client, **changes):
    created = client.post(
        "/api/trips",
        json={
            "start_date": "2026-10-24",
            "days": 3,
            "lodging_mode": "route_first",
            **changes,
        },
    )
    assert created.status_code == 201, created.text
    trip_id = created.json()["id"]
    run_id = client.post(f"/api/trips/{trip_id}/plan-runs").json()["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed", run
    assert confirm(client, trip_id, run["proposal"]).status_code == 200
    return trip_id, run["proposal"]["itinerary"]


def confirm(client, trip_id, proposal):
    return client.post(
        f"/api/trips/{trip_id}/proposals/{proposal['id']}/apply",
        json={
            "base_version": proposal["base_version"],
            "idempotency_key": "confirm-" + proposal["id"],
        },
    )


def test_arrival_and_departure_are_hard_route_and_time_boundaries():
    requirements = req(arrival=boundary("抵达车站", "11:00", 30), departure=boundary("返程机场", "16:00", 60))
    result = draft(requirements)
    assert result.days[0].legs[0].origin_id == "arrival"
    assert result.days[-1].legs[-1].destination_id == "departure"
    first = result.days[0]
    assert minutes(first.activities[0].start) >= 11 * 60 + 30 + first.legs[0].duration_minutes
    last = result.days[-1]
    assert minutes(last.activities[-1].end) + last.legs[-1].duration_minutes <= 15 * 60
    assert not any(c.code == "arrival_departure_unknown" for c in result.checks)
    assert not any(c.severity == "error" for c in result.checks)


def test_independent_validator_rejects_arriving_before_train_and_missing_airport_connection():
    requirements = req(arrival=boundary("车站", "11:00", 30), departure=boundary("机场", "16:00", 60))
    result = draft(requirements)
    result.days[0].activities[0].start = "09:00"
    result.days[-1].legs.pop()
    codes = {c.code for c in validate_itinerary(requirements, result).checks if c.severity == "error"}
    assert {"late_arrival", "route_coverage"} <= codes


def test_per_day_window_and_delay_cannot_bypass_arrival():
    requirements = req(
        arrival=boundary("车站", "13:00", 30), day_windows={"1": {"start": "08:00", "end": "20:00"}}
    )
    assert boundary_window(requirements, 1, {"1": -60}) == (13 * 60 + 30, 20 * 60)


def test_late_arrival_and_early_departure_keep_all_days_and_hotel_connections(client):
    trip_id, result = save_draft(
        client,
        arrival=boundary("晚到车站", "20:30", 30),
        departure=boundary("早班机场", "08:00", 60),
    )
    assert not result["days"][0]["activities"] and not result["days"][2]["activities"]
    assert len(result["days"][1]["activities"]) >= 3
    assert result["lodging"]["nights"][0]["last_place_id"] == "arrival"
    assert result["lodging"]["nights"][1]["next_place_id"] == "departure"
    for night in (1, 2):
        selected = client.post(
            f"/api/trips/{trip_id}/hotel-selections",
            json={
                "base_version": night,
                "night": night,
                "candidate_id": result["lodging"]["nights"][night - 1]["candidates"][0]["id"],
            },
        )
        assert selected.status_code == 202, selected.text
        run = client.get(f"/api/runs/{selected.json()['run_id']}").json()
        assert run["status"] == "completed", run
        result = run["proposal"]["itinerary"]
        assert not any(check["severity"] == "error" for check in result["checks"])
        assert confirm(client, trip_id, run["proposal"]).status_code == 200
    assert len(result["days"][0]["legs"]) == len(result["days"][2]["legs"]) == 1
    assert result["days"][0]["legs"][0]["origin_id"] == "arrival"
    assert result["days"][2]["legs"][0]["destination_id"] == "departure"


def test_revision_refreshes_hotel_distances_for_boundary_only_days():
    requirements = req(arrival=boundary("晚到车站", "20:30", 30))
    base = draft(requirements)
    removable = base.days[1].activities[0].place_id
    _, result = revise_route(
        requirements,
        base,
        RevisionIntent(
            summary="移除次日首站", operations=[EditOperation(kind="remove", day=2, place_id=removable)]
        ),
        TravelTools(settings()),
        CATALOG,
    )
    assert result.lodging.nights[0].last_place_id == "arrival"
    assert result.lodging.nights[0].next_place_id == result.days[1].activities[0].place_id


def test_demo_location_search_is_explicit_and_real_search_returns_chosen_poi(client, monkeypatch):
    body = {"city": "杭州", "query": "杭州东站", "center": boundary("中心", "10:00", 0)["location"]}
    demo = client.post("/api/locations/search", json=body).json()
    assert "演示" in demo["note"] and "演示" in demo["locations"][0]["name"]
    monkeypatch.setattr(
        TravelTools,
        "_get",
        lambda *_: {
            "pois": [
                {"name": "杭州东站", "location": "120.2,30.3"},
                {"name": "坏坐标", "location": "unknown"},
            ]
        },
    )
    headers = {"x-travel-config": json.dumps({"tool_provider": "amap", "amap_api_key": "fixture"})}
    real = client.post("/api/locations/search", json=body, headers=headers)
    assert real.status_code == 200 and len(real.json()["locations"]) == 1
    assert real.json()["locations"][0]["coordinate_system"] == "GCJ-02"


def test_demo_natural_request_applies_all_operations_and_preserves_other_days():
    base = draft()
    day = base.days[0]
    removable = day.activities[-1].place_id
    message = f"第1天晚出发20分钟，第1天不去{CATALOG[removable].name}"
    tools = TravelTools(settings())
    intent = interpret_revision(message, req(), base, CATALOG, tools)
    assert len(intent.operations) == 2
    _, result = revise_route(req(), base, intent, tools, CATALOG, user_message=message)
    assert result.day_start_offsets["1"] == 20
    assert removable not in [a.place_id for a in result.days[0].activities]
    assert result.days[1:] == base.days[1:]


@pytest.mark.parametrize("delay,locked,conflict", [(40, False, False), (180, False, True), (40, True, True)])
def test_departure_delay_preserves_stops_breaks_hotels_and_locks_without_requery(
    monkeypatch, delay, locked, conflict
):
    requirements = req()
    base = draft(requirements)
    for night in (1, 2):
        base = select_hotel(
            requirements,
            base,
            HotelChoiceRequest(
                base_version=night, night=night, candidate_id=base.lodging.nights[night - 1].candidates[0].id
            ),
            TravelTools(settings()),
            CATALOG,
        )
    catalog = dict(CATALOG)
    times = [("09:30", "11:00"), ("11:31", "12:16"), ("13:05", "14:35"), ("15:05", "16:35")]
    ids = [f"delay-stop-{i}" for i in range(4)]
    for pid in ids:
        catalog[pid] = CATALOG["westlake"].model_copy(
            update={"id": pid, "name": pid, "opening": None, "closing": None}
        )
    base.days[1].activities = [
        Activity(
            id=pid,
            place_id=pid,
            day=2,
            start=start,
            end=end,
            locked=locked and index == 1,
            reason="已确认的安排",
        )
        for index, (pid, (start, end)) in enumerate(zip(ids, times, strict=True))
    ]
    endpoints = ["hotel-night-1", *ids, "hotel-night-2"]
    base.days[1].legs = [
        RouteLeg(
            origin_id=a,
            destination_id=b,
            duration_minutes=20,
            distance_meters=1000,
            provider="demo",
            evidence="已查询路线",
        )
        for a, b in zip(endpoints, endpoints[1:], strict=False)
    ]
    assert not any(
        c.severity == "error" for c in validate_itinerary(requirements, base, catalog=catalog).checks
    )
    tools = TravelTools(settings())

    def no_requery(*_):
        raise AssertionError("A departure-only change must reuse the saved routes and weather")

    monkeypatch.setattr(tools, "route", no_requery)
    monkeypatch.setattr(tools, "weather", no_requery)
    _, result = revise_route(
        requirements,
        base,
        RevisionIntent(
            summary="第二天推迟出发", operations=[EditOperation(kind="delay", day=2, minutes=delay)]
        ),
        tools,
        catalog,
    )
    assert [a.id for a in result.days[1].activities] == ids
    assert result.days[1].legs == base.days[1].legs
    assert result.days[0] == base.days[0] and result.days[2] == base.days[2]
    assert [stay.selected_id for stay in result.lodging.nights] == [
        stay.selected_id for stay in base.lodging.nights
    ]
    assert any(c.severity == "error" for c in result.checks) == conflict
    if locked:
        assert result.days[1].activities[1] == base.days[1].activities[1]
    elif not conflict:
        assert [a.start for a in result.days[1].activities] == ["10:10", "12:11", "13:45", "15:45"]
        assert result.days[1].activities[-1].end == "17:15"


@pytest.mark.parametrize("bad", ["unknown-place", "westlake"])
def test_unknown_or_locked_edits_are_rejected(bad):
    base = draft()
    found = next((a for d in base.days for a in d.activities if a.place_id == bad), None)
    if found:
        found.locked = True
    with pytest.raises(ToolUnavailable):
        revise_route(
            req(),
            base,
            RevisionIntent(
                summary="移除",
                operations=[
                    EditOperation(kind="remove", day=1, place_id=bad),
                ],
            ),
            TravelTools(settings()),
            CATALOG,
            user_message="不去西湖",
        )


def test_natural_model_input_has_structured_state_and_does_not_receive_raw_guide_pages(monkeypatch):
    base = draft()
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "第二天晚出发",
                                    "operations": [{"kind": "delay", "day": 2, "minutes": 30}],
                                }
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("httpx.post", post)
    runtime = Settings(
        planning_mode="llm",
        llm_base_url="https://model.example/v1",
        llm_model="fixture",
        llm_api_key="secret-fixture",
        _env_file=None,
    )
    intent = interpret_revision("第二天晚出发半小时，酒店不变", req(), base, CATALOG, TravelTools(runtime))
    assert intent.operations[0].minutes == 30 and len(requests) == 1
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert "selected_hotels" in payload and "guides" not in payload
    assert "secret-fixture" not in json.dumps(payload)


def test_clarification_and_partial_demo_understanding_never_produce_a_partial_change():
    base = draft()
    with pytest.raises(ToolUnavailable, match="补充"):
        revise_route(
            req(),
            base,
            RevisionIntent(summary="有歧义", clarification="你指的是哪一天？"),
            TravelTools(settings()),
            CATALOG,
        )
    with pytest.raises(ToolUnavailable, match="演示"):
        interpret_revision(
            "第1天晚出发20分钟，并给我订一家酒店", req(), base, CATALOG, TravelTools(settings())
        )


def test_absolute_daily_start_replaces_previous_relative_delay():
    base = draft()
    base.day_start_offsets["2"] = 30
    updated, result = revise_route(
        req(),
        base,
        RevisionIntent(
            summary="第二天恢复九点开始", operations=[EditOperation(kind="day_window", day=2, start="09:00")]
        ),
        TravelTools(settings()),
        CATALOG,
    )
    assert result.day_start_offsets["2"] == 0
    assert boundary_window(updated, 2, result.day_start_offsets)[0] == 9 * 60


def test_requirements_changes_are_previewed_versioned_and_restored(client, monkeypatch):
    trip_id, _ = save_draft(client, departure=boundary("机场", "18:00", 30))
    intent = RevisionIntent(
        summary="最后一天提前返程", operations=[EditOperation(kind="departure", at="16:30")]
    )
    monkeypatch.setattr("app.services.interpret_revision", lambda *_: intent)
    started = client.post(
        f"/api/trips/{trip_id}/revisions", json={"base_version": 1, "message": "返程改成16:30"}
    )
    assert started.status_code == 202
    run = client.get(f"/api/runs/{started.json()['run_id']}").json()
    assert run["status"] == "completed", run
    proposal = run["proposal"]
    assert proposal["requirements"]["departure"]["time"] == "16:30:00"
    assert client.get(f"/api/trips/{trip_id}").json()["requirements"]["departure"]["time"] == "18:00:00"
    assert confirm(client, trip_id, proposal).status_code == 200
    assert client.get(f"/api/trips/{trip_id}").json()["requirements"]["departure"]["time"] == "16:30:00"
    restored = client.post(
        f"/api/trips/{trip_id}/versions/1/restore",
        json={
            "base_version": 2,
            "idempotency_key": "restore-earlier-requirements",
        },
    )
    assert restored.status_code == 200
    assert client.get(f"/api/trips/{trip_id}").json()["requirements"]["departure"]["time"] == "18:00:00"


def test_user_can_explicitly_remove_a_must_visit_but_model_cannot_do_it_unasked():
    requirements = req(must_visit=["westlake"])
    base = draft(requirements)
    day = next(d.day for d in base.days if any(a.place_id == "westlake" for a in d.activities))
    intent = RevisionIntent(
        summary="移除西湖", operations=[EditOperation(kind="remove", day=day, place_id="westlake")]
    )
    with pytest.raises(ToolUnavailable, match="必去"):
        revise_route(
            requirements, base, intent, TravelTools(settings()), CATALOG, user_message="晚出发一会儿"
        )
    updated, result = revise_route(
        requirements, base, intent, TravelTools(settings()), CATALOG, user_message="不去西湖了"
    )
    assert "westlake" not in updated.must_visit
    assert all(a.place_id != "westlake" for d in result.days for a in d.activities)


def test_hotel_change_runs_llm_review_and_preserves_selected_hotel(monkeypatch):
    base = draft()
    candidate = base.lodging.nights[0].candidates[0]
    base = select_hotel(
        req(),
        base,
        HotelChoiceRequest(base_version=1, night=1, candidate_id=candidate.id),
        TravelTools(settings()),
        CATALOG,
    )
    calls = []

    def fake(*args):
        calls.append(args)
        return HotelReview(decision="keep", rationale="交通有足够余量，保留原景点安排。")

    monkeypatch.setattr("app.revisions.model_json", fake)
    runtime = Settings(planning_mode="llm", _env_file=None)
    result = review_hotel_choice(req(), base, 1, TravelTools(runtime), CATALOG)
    assert len(calls) == 1 and result.revision_status == "model_reviewed"
    assert result.lodging.nights[0].selected_id == candidate.id


def test_hotel_review_can_reorder_but_cannot_edit_user_boundaries(monkeypatch):
    base = draft()
    ids = [a.place_id for a in base.days[1].activities]
    runtime = Settings(planning_mode="llm", _env_file=None)
    monkeypatch.setattr(
        "app.revisions.model_json",
        lambda *_: HotelReview(
            decision="adjust",
            rationale="调整同日游玩顺序。",
            operations=[EditOperation(kind="reorder", day=2, place_ids=list(reversed(ids)))],
        ),
    )
    result = review_hotel_choice(req(), base, 1, TravelTools(runtime), CATALOG)
    assert result.revision_status == "model_reviewed"
    assert [a.place_id for a in result.days[1].activities] == list(reversed(ids))
    assert result.days[2] == base.days[2]
    monkeypatch.setattr(
        "app.revisions.model_json",
        lambda *_: HotelReview(
            decision="adjust",
            rationale="扩大出发时间。",
            operations=[EditOperation(kind="delay", day=1, minutes=-120)],
        ),
    )
    refused = review_hotel_choice(req(), base, 1, TravelTools(runtime), CATALOG)
    assert refused.revision_status == "unavailable" and refused.days == base.days


def test_migration_backfills_legacy_requirements_without_changing_old_itinerary(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}"
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0003")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO trips (id, requirements, catalog, current_version, created_at) VALUES "
                "('trip', :requirements, '{}', 1, 'before')"
            ),
            {"requirements": json.dumps({"days": 3})},
        )
        db.execute(
            text(
                "INSERT INTO versions (id,trip_id,number,itinerary,label,created_at) VALUES "
                "('version','trip',1,'{\"days\":[]}', 'old','before')"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as db:
        row = db.execute(text("SELECT requirements, itinerary FROM versions")).one()
        assert json.loads(row[0]) == {"days": 3} and json.loads(row[1]) == {"days": []}
    engine.dispose()
