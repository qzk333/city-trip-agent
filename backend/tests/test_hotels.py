import json

import httpx
import pytest

from app.agent import generate
from app.catalog import CATALOG
from app.config import Settings
from app.hotel_agent import AmapHotelProvider, rank_hotels, research_hotels
from app.llm import model_candidates
from app.schemas import Itinerary, Requirements
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def route_requirements(**changes):
    return Requirements(start_date="2026-10-24", days=3, lodging_mode="route_first", **changes)


def draft(req=None):
    return generate(req or route_requirements(), Settings(_env_file=None), lambda *_: None)


def create_saved_draft(client, **changes):
    trip = client.post(
        "/api/trips",
        json={
            "start_date": "2026-10-24",
            "days": 3,
            "lodging_mode": "route_first",
            **changes,
        },
    ).json()
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed", run
    proposal = run["proposal"]
    saved = client.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/apply",
        json={
            "base_version": 0,
            "idempotency_key": "save-route-draft",
        },
    )
    assert saved.status_code == 200, saved.text
    return trip["id"], proposal["itinerary"], run_id


def choose(client, trip_id, base, version=1, night=1, **changes):
    body = {
        "base_version": version,
        "night": night,
        "candidate_id": base["lodging"]["nights"][night - 1]["candidates"][0]["id"],
        **changes,
    }
    if changes.get("custom_location"):
        body.pop("candidate_id")
    started = client.post(f"/api/trips/{trip_id}/hotel-selections", json=body)
    assert started.status_code == 202, started.text
    run = client.get(f"/api/runs/{started.json()['run_id']}").json()
    assert run["status"] == "completed", run
    return run


def confirm(client, trip_id, proposal):
    return client.post(
        f"/api/trips/{trip_id}/proposals/{proposal['id']}/apply",
        json={
            "base_version": proposal["base_version"],
            "idempotency_key": "confirm-" + proposal["id"],
        },
    )


def test_route_draft_does_not_query_or_return_to_placeholder_hotel(monkeypatch):
    queried = []
    real_route = TravelTools.route

    def route(self, origin_id, a, destination_id, b, mode):
        queried.append((origin_id, destination_id))
        return real_route(self, origin_id, a, destination_id, b, mode)

    monkeypatch.setattr(TravelTools, "route", route)
    result = draft()
    assert all(day.activities and len(day.legs) == len(day.activities) - 1 for day in result.days)
    assert all("hotel" not in str(pair) for pair in queried)
    assert not any(check.severity == "error" for check in result.checks)
    assert any(check.code == "lodging_pending" for check in result.checks)


def test_hotel_nights_follow_daily_last_and_next_first_and_have_no_fake_price():
    result = draft()
    assert result.lodging.status == "ready" and len(result.lodging.nights) == 2
    for index, stay in enumerate(result.lodging.nights):
        assert stay.last_place_id == result.days[index].activities[-1].place_id
        assert stay.next_place_id == result.days[index + 1].activities[0].place_id
        assert stay.checkin == result.days[index].date and stay.checkout == result.days[index + 1].date
        assert stay.candidates and stay.selected_id is None
        for hotel in stay.candidates:
            assert hotel.provider == "demo" and hotel.commute_kind == "distance_estimate"
            assert "报价" in hotel.price_note
            assert not {"price", "cost", "availability"} & hotel.model_dump().keys()


def test_amap_hotel_provider_uses_bounded_shared_gateway_and_filters_bad_pois(monkeypatch):
    tools = TravelTools(Settings(tool_provider="amap", _env_file=None))
    requests = []

    def get(path, params):
        requests.append((path, params))
        return {
            "pois": [
                {
                    "id": "H1",
                    "name": "真实酒店",
                    "typecode": "100100",
                    "cityname": "杭州市",
                    "location": "120.16,30.25",
                    "address": "测试地址",
                    "biz_ext": {"cost": "999"},
                },
                {"id": "bad", "name": "餐厅", "typecode": "050100", "location": "120.16,30.25"},
                {
                    "id": "other",
                    "name": "异地酒店",
                    "typecode": "100100",
                    "cityname": "广州市",
                    "location": "1,2",
                },
                {"id": "wrong", "name": "坏坐标", "typecode": "100100", "location": "999,999"},
            ]
        }

    monkeypatch.setattr(tools, "_get", get)
    rows = AmapHotelProvider(tools).search(CATALOG["westlake"])
    assert [row["id"] for row in rows] == ["amap-H1"]
    assert requests[0][0] == "/v3/place/around" and requests[0][1]["types"] == "100000"
    assert requests[0][1]["offset"] == 6
    assert "999" not in json.dumps(rows, default=str)  # POI cost is not an accommodation quote.


def test_hotel_query_failure_keeps_route_and_never_invents_real_hotel():
    class FailingProvider:
        def search(self, anchor):
            raise ToolUnavailable("hotel lookup failed")

    result = draft()
    before = [day.model_dump() for day in result.days]
    research = research_hotels(
        route_requirements(),
        result,
        CATALOG,
        TravelTools(Settings(_env_file=None)),
        provider=FailingProvider(),
    )
    assert research.status == "unavailable" and all(not n.candidates for n in research.nights)
    assert before == [day.model_dump() for day in result.days]


@pytest.mark.parametrize("failure", ["unknown_id", "duplicate", "wrong_night", "http_error"])
def test_hotel_agent_rejects_bad_rankings_and_keeps_valid_provider_candidates(monkeypatch, failure):
    result = draft()
    original = result.lodging.model_copy(deep=True)
    payload = {
        "nights": [
            {"night": n.night, "hotel_ids": [h.id for h in n.candidates]} for n in result.lodging.nights
        ]
    }
    if failure == "unknown_id":
        payload["nights"][0]["hotel_ids"][0] = "invented-hotel"
    elif failure == "duplicate":
        payload["nights"][0]["hotel_ids"][1] = payload["nights"][0]["hotel_ids"][0]
    elif failure == "wrong_night":
        payload["nights"][1]["night"] = 1
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(
            429 if failure == "http_error" else 200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}], "secret": "do-not-store"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("httpx.post", post)
    settings = Settings(planning_mode="llm", llm_model="fixture", _env_file=None)
    tools = TravelTools(settings)
    rank_hotels(result.lodging, {"city": "杭州"}, settings, tools)
    assert len(calls) == 1 and result.lodging.analysis_status == "unavailable"
    assert result.lodging.nights == original.nights
    assert "do-not-store" not in json.dumps(tools.trace)


def test_hotel_agent_context_contains_only_its_task_and_keeps_user_choice_empty(monkeypatch):
    result = draft()
    calls = []
    payload = {
        "nights": [
            {"night": n.night, "hotel_ids": [h.id for h in reversed(n.candidates)]}
            for n in result.lodging.nights
        ]
    }

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("httpx.post", post)
    settings = Settings(planning_mode="llm", llm_model="fixture", _env_file=None)
    rank_hotels(
        result.lodging, {"city": "杭州", "preferences": "希望少折返"}, settings, TravelTools(settings)
    )
    assert result.lodging.analysis_status == "ok"
    assert all(n.selected_id is None for n in result.lodging.nights)
    content = json.loads(calls[0]["messages"][1]["content"])
    assert set(content) == {"task", "hotel_evidence"}
    assert "tools" not in calls[0] and len(calls[0]["messages"]) == 2


def test_single_day_does_not_search_hotels_or_make_model_calls(monkeypatch):
    def unexpected(*_, **__):
        pytest.fail("Single-day itineraries have no overnight stay")

    monkeypatch.setattr(AmapHotelProvider, "search", unexpected)
    result = draft(Requirements(start_date="2026-10-24", lodging_mode="route_first"))
    assert result.lodging.status == "not_needed" and not result.lodging.nights


def test_user_selection_is_a_preview_persists_and_only_replans_adjacent_days(client, monkeypatch):
    trip_id, base, run_id = create_saved_draft(client)
    assert [t["role"] for t in client.get(f"/api/runs/{run_id}/agent-tasks").json()] == [
        "guide",
        "main",
        "hotel",
    ]

    def unexpected(*_, **__):
        pytest.fail("Hotel selection must not rerun guide, model or hotel research")

    monkeypatch.setattr("app.agent.research_guides", unexpected)
    monkeypatch.setattr("app.agent.research_hotels", unexpected)
    monkeypatch.setattr("httpx.post", unexpected)
    run = choose(client, trip_id, base)
    proposal = run["proposal"]
    changed = proposal["itinerary"]
    assert client.get(f"/api/trips/{trip_id}").json()["current_version"] == 1
    assert changed["days"][2] == base["days"][2]
    assert [a["place_id"] for d in changed["days"] for a in d["activities"]] == [
        a["place_id"] for d in base["days"] for a in d["activities"]
    ]
    assert changed["days"][0]["legs"][-1]["destination_id"] == "hotel-night-1"
    assert changed["days"][1]["legs"][0]["origin_id"] == "hotel-night-1"
    assert [task["role"] for task in run["agent_tasks"]] == ["main"]
    assert confirm(client, trip_id, proposal).json()["version"] == 2
    assert confirm(client, trip_id, proposal).json()["replayed"] is True
    saved = client.get(f"/api/trips/{trip_id}").json()
    assert saved["itinerary"]["lodging"] == changed["lodging"]


def test_unknown_hotel_and_stale_version_are_rejected_before_background_work(client):
    trip_id, base, _ = create_saved_draft(client)
    url = f"/api/trips/{trip_id}/hotel-selections"
    assert (
        client.post(url, json={"base_version": 1, "night": 1, "candidate_id": "invented"}).status_code == 422
    )
    assert (
        client.post(
            url,
            json={
                "base_version": 2,
                "night": 1,
                "candidate_id": base["lodging"]["nights"][0]["candidates"][0]["id"],
            },
        ).status_code
        == 409
    )
    assert client.post(url, json={"base_version": 1, "night": 1}).status_code == 422


def test_custom_hotel_can_be_chosen_and_restored_through_version_history(client):
    trip_id, base, _ = create_saved_draft(client)
    place = CATALOG[base["days"][1]["activities"][0]["place_id"]]
    run = choose(
        client,
        trip_id,
        base,
        custom_location={
            "name": "我已订的酒店",
            "longitude": place.longitude,
            "latitude": place.latitude,
        },
    )
    assert confirm(client, trip_id, run["proposal"]).status_code == 200
    assert run["proposal"]["itinerary"]["lodging"]["nights"][0]["selected_id"] == "user-hotel-night-1"
    restored = client.post(
        f"/api/trips/{trip_id}/versions/1/restore",
        json={
            "base_version": 2,
            "idempotency_key": "restore-without-hotel",
        },
    )
    assert restored.status_code == 200
    assert (
        client.get(f"/api/trips/{trip_id}").json()["itinerary"]["lodging"]["nights"][0]["selected_id"] is None
    )


def test_locked_first_activity_is_not_moved_when_hotel_makes_it_unreachable(client):
    trip_id, base, _ = create_saved_draft(client)
    first = base["days"][1]["activities"][0]
    started = client.post(
        f"/api/trips/{trip_id}/proposals",
        json={
            "base_version": 1,
            "action": "lock",
            "day": 2,
            "activity_id": first["id"],
        },
    ).json()
    locked = client.get(f"/api/runs/{started['run_id']}").json()["proposal"]
    assert confirm(client, trip_id, locked).status_code == 200
    run = choose(client, trip_id, locked["itinerary"], version=2)
    changed_first = run["proposal"]["itinerary"]["days"][1]["activities"][0]
    assert changed_first["start"] == first["start"] and changed_first["locked"] is True
    assert any(c["code"] == "late_arrival" for c in run["proposal"]["itinerary"]["checks"])
    assert confirm(client, trip_id, run["proposal"]).status_code == 422


def test_day_adjustment_preserves_selected_hotel_and_other_days(client):
    trip_id, base, _ = create_saved_draft(client)
    selected = choose(client, trip_id, base)["proposal"]
    assert confirm(client, trip_id, selected).status_code == 200
    started = client.post(
        f"/api/trips/{trip_id}/proposals",
        json={
            "base_version": 2,
            "action": "delay",
            "day": 1,
            "delay_minutes": 20,
        },
    ).json()
    run = client.get(f"/api/runs/{started['run_id']}").json()
    assert run["status"] == "completed", run
    result = run["proposal"]["itinerary"]
    assert (
        result["lodging"]["nights"][0]["selected_id"]
        == selected["itinerary"]["lodging"]["nights"][0]["selected_id"]
    )
    assert result["days"][1:] == selected["itinerary"]["days"][1:]
    req = Requirements(**client.get(f"/api/trips/{trip_id}").json()["requirements"])
    assert not [c for c in validate_itinerary(req, Itinerary(**result)).checks if c.severity == "error"]


def test_changing_one_night_reuses_other_hotel_legs_and_only_queries_two_boundaries(client, monkeypatch):
    trip_id, base, _ = create_saved_draft(client)
    first = choose(client, trip_id, base)["proposal"]
    assert confirm(client, trip_id, first).status_code == 200
    second = choose(client, trip_id, first["itinerary"], version=2, night=2)["proposal"]
    assert confirm(client, trip_id, second).status_code == 200
    previous = second["itinerary"]
    queried = []
    real_route = TravelTools.route

    def route(self, origin_id, a, destination_id, b, mode):
        if mode == "auto":
            queried.append((origin_id, destination_id))
        return real_route(self, origin_id, a, destination_id, b, mode)

    monkeypatch.setattr(TravelTools, "route", route)
    alternate = previous["lodging"]["nights"][0]["candidates"][1]["id"]
    changed = choose(client, trip_id, previous, version=3, candidate_id=alternate)["proposal"]["itinerary"]
    assert len(queried) == 2
    assert queried[0][1] == "hotel-night-1" and queried[1][0] == "hotel-night-1"
    assert changed["days"][1]["legs"][-1] == previous["days"][1]["legs"][-1]
    assert changed["days"][2] == previous["days"][2]
    assert not any(c["code"] == "lodging_pending" for c in changed["checks"])


def test_extremely_distant_custom_hotel_produces_conflict_preview_instead_of_invalid_times(client):
    trip_id, base, _ = create_saved_draft(client)
    run = choose(
        client,
        trip_id,
        base,
        custom_location={
            "name": "明显不适合本路线的远方酒店",
            "longitude": -120,
            "latitude": 30,
        },
    )
    proposal = run["proposal"]
    assert proposal["itinerary"]["status"] == "NO_SOLUTION_WITHIN_LIMIT"
    assert confirm(client, trip_id, proposal).status_code == 422
    assert client.get(f"/api/trips/{trip_id}").json()["current_version"] == 1


def test_main_agent_does_not_receive_placeholder_hotel_in_route_first_mode(monkeypatch):
    requests = []
    messages = [
        {"tool_calls": [{"id": "all", "function": {"name": "search_places", "arguments": '{"query":""}'}}]},
        {"content": json.dumps({"place_ids": list(CATALOG)})},
    ]

    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(
            200, json={"choices": [{"message": messages.pop(0)}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("httpx.post", post)
    model_candidates(
        route_requirements(),
        Settings(
            planning_mode="llm",
            llm_base_url="https://model.example/v1",
            llm_api_key="fixture",
            llm_model="fixture",
            _env_file=None,
        ),
        [],
        lambda *_: None,
    )
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["requirements"]["lodging_mode"] == "route_first"
    assert "hotel" not in payload["requirements"]
