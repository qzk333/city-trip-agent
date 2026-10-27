import json

import pytest

from app.agent import generate
from app.candidate_discovery import query_plan
from app.candidate_ranking import rank_candidates, select_for_requirements
from app.catalog import CATALOG
from app.config import Settings
from app.database import CatalogRecord
from app.place_evidence import effective_indoor, provider_place
from app.schemas import Place, Requirements, TravelProfile
from app.spatial_planning import group_candidates
from app.tools import ToolUnavailable, TravelTools, distance_meters
from app.travel_profile import normalize_profile


def poi(pid, name, category="culture", lon=120.16, lat=30.25):
    return provider_place(
        {
            "id": pid,
            "name": name,
            "location": f"{lon},{lat}",
            "typecode": {"culture": "140100", "nature": "110100", "coffee": "050500", "city": "060100"}[
                category
            ],
            "cityname": "杭州市",
        },
        category,
        "杭州",
    )


def test_profiles_change_queries_without_inventing_mobility_requirements():
    history = normalize_profile(TravelProfile(), "带父母，喜欢历史，少走路，不去商场")
    night = normalize_profile(TravelProfile(), "喜欢夜景拍照，不喜欢咖啡")
    assert history.companions == "parents" and history.walking == "less"
    assert history.excluded_keywords == ["商场"]
    assert "coffee" not in night.themes
    assert query_plan(history) != query_plan(night)
    assert normalize_profile(TravelProfile(), "和父母出游").walking == "normal"


def test_real_place_attributes_remain_unknown_and_old_snapshots_still_decode():
    place = poi("museum", "历史博物馆")
    assert place.traits.indoor_outdoor == "unknown" and not effective_indoor(place)
    assert place.traits.duration_min < place.duration_minutes < place.traits.duration_max
    assert place.opening is None
    assert {e.kind for e in place.traits.evidence} == {"observed", "inferred"}
    legacy = place.model_dump(exclude={"traits", "candidate_match"})
    legacy["indoor"] = True
    assert not effective_indoor(Place(**legacy))
    assert effective_indoor(CATALOG["gushan_museum"])


def test_relevance_diversity_and_protected_excluded_decision():
    catalog = {
        p.id: p
        for p in [
            poi("h", "历史博物馆"),
            poi("n", "滨江夜景", "city"),
            poi("c1", "咖啡一店", "coffee"),
            poi("c2", "咖啡二店", "coffee"),
        ]
    }
    historical = rank_candidates(catalog, TravelProfile(themes=["history"]))
    night = rank_candidates(catalog, TravelProfile(themes=["night"]))
    assert next(iter(historical)) == "amap-h" and next(iter(night)) == "amap-n"
    assert list(historical).index("amap-c1") > 0
    filtered = rank_candidates(catalog, TravelProfile(excluded_keywords=["咖啡"]), protected=["amap-c2"])
    assert "amap-c1" not in filtered and "amap-c2" in filtered
    assert filtered["amap-c2"].candidate_match.reasons == ["保留你明确选择的地点"]


def test_shortlist_cannot_drop_protected_outlier():
    catalog = {
        p.id: p
        for p in [*(poi(str(i), f"历史馆{i}") for i in range(20)), poi("remote", "远方指定地点", lon=121)]
    }
    req = Requirements(start_date="2026-10-26", must_visit=["amap-remote"])
    selected = select_for_requirements(req, catalog)
    assert len(selected) == 8 and "amap-remote" in selected


def test_spatial_groups_cluster_but_preserve_date_pins_and_closed_first_day():
    places = [
        poi(f"{region}{i}", f"历史馆{region}{i}", lon=120.1 + region * 0.15 + i * 0.001)
        for i in range(3)
        for region in range(2)
    ]
    catalog = {p.id: p for p in places}
    req = Requirements(start_date="2026-10-26", days=2, lodging_mode="route_first")
    groups = group_candidates(req, catalog, list(catalog))
    assert all(len(group) == 3 for group in groups)
    assert all(
        max(catalog[p].longitude for p in g) - min(catalog[p].longitude for p in g) < 0.01 for g in groups
    )
    req.place_days = {"amap-00": 2}
    groups = group_candidates(req, catalog, list(catalog))
    assert "amap-00" in groups[1] and "amap-00" not in groups[0]


def discovery_body(**extra):
    return {"city_id": 19598, "start_date": "2026-10-26", "days": 2, "note": "带父母，历史，少走路", **extra}


def test_recommendations_keep_choices_store_metadata_and_reject_stale_requirements(client):
    response = client.post("/api/places/recommendations", json=discovery_body())
    assert response.status_code == 200, response.text
    result = response.json()
    chosen = result["places"][0]["id"]
    refreshed = client.post(
        "/api/places/recommendations",
        json=discovery_body(catalog_id=result["catalog_id"], keep_ids=[chosen], note="夜景拍照"),
    )
    assert refreshed.status_code == 200
    assert chosen in {p["id"] for p in refreshed.json()["places"]}
    with client.app.state.session_factory() as db:
        snapshot = db.get(CatalogRecord, refreshed.json()["catalog_id"])
        assert snapshot.discovery["strategy"] == "demand-v1"
        assert snapshot.discovery["profile"]["themes"] == ["night", "photo"]
    payload = discovery_body(catalog_id=result["catalog_id"])
    assert client.post("/api/trips", json=payload).status_code == 201
    payload["note"] = "夜景"
    assert client.post("/api/trips", json=payload).status_code == 422
    assert (
        client.post(
            "/api/places/recommendations",
            json=discovery_body(catalog_id=result["catalog_id"], keep_ids=["foreign-id"]),
        ).status_code
        == 422
    )


def test_map_discovery_filters_wrong_city_and_duplicates_and_has_bounded_queries(client, monkeypatch):
    from test_destinations_and_config import headers

    calls = []

    def get(self, path, params):
        calls.append((path, params))
        if path.endswith("geo"):
            return {"geocodes": [{"adcode": "330100", "location": "120.16,30.25"}]}
        return {
            "pois": [
                {
                    "id": "valid",
                    "name": "历史博物馆",
                    "location": "120.16,30.25",
                    "cityname": "杭州市",
                    "typecode": "140100",
                    "adcode": "330106",
                },
                {
                    "id": "other",
                    "name": "另一城市",
                    "location": "120.1,30.2",
                    "cityname": "成都市",
                    "typecode": "140100",
                },
                {"id": "station", "name": "历史博物馆站", "location": "120.16,30.25", "typecode": "150500"},
            ]
        }

    monkeypatch.setattr(TravelTools, "_get", get)
    response = client.post(
        "/api/places/recommendations",
        json=discovery_body(),
        headers=headers(tool_provider="amap", amap_api_key="not-a-real-key"),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert [p["id"] for p in result["places"]] == ["amap-valid"]
    assert len(calls) <= 9 and all(p["offset"] <= 6 for _, p in calls[1:])
    assert "not-a-real-key" not in json.dumps(result)
    assert result["places"][0]["traits"]["indoor_outdoor"] == "unknown"


def test_fatal_provider_error_never_degrades_to_demo(client, monkeypatch):
    from test_destinations_and_config import headers

    def get(*_):
        raise ToolUnavailable("高德权限不足", fatal=True)

    monkeypatch.setattr(TravelTools, "_get", get)
    result = client.post(
        "/api/places/recommendations",
        json=discovery_body(),
        headers=headers(tool_provider="amap", amap_api_key="fixture"),
    )
    assert result.status_code == 400 and "权限不足" in result.json()["detail"]


def test_independent_validator_blocks_excluded_protected_place():
    catalog = {"amap-coffee": poi("coffee", "咖啡馆", "coffee")}
    req = Requirements(
        start_date="2026-10-26", must_visit=["amap-coffee"], profile=TravelProfile(excluded_keywords=["咖啡"])
    )
    result = generate(req, Settings(_env_file=None), lambda *_: None, catalog=catalog)
    assert any(c.code == "excluded_place" and c.severity == "error" for c in result.checks)


def test_spatial_selection_does_not_query_all_pairs_or_reintroduce_excluded(monkeypatch):
    catalog = {
        p.id: p
        for p in [
            *(poi(str(i), f"历史博物馆{i}", lon=120.15 + i * 0.002) for i in range(18)),
            poi("mall", "大型商场", "city"),
        ]
    }
    req = Requirements(start_date="2026-10-26", days=3, note="不去商场", lodging_mode="route_first")
    result = generate(req, Settings(_env_file=None), lambda *_: None, catalog=catalog)
    spatial = next(t for t in result.trace if t["tool"] == "spatial_selection")
    assert "amap-mall" not in spatial["allowed_place_ids"]
    assert len(spatial["allowed_place_ids"]) <= 24
    assert all(a.place_id != "amap-mall" for d in result.days for a in d.activities)
    assert len([t for t in result.trace if t["tool"] == "get_routes"]) < 3 * len(catalog)
    assert all(d.activities for d in result.days)


def test_mixed_coordinate_systems_do_not_invent_short_distances():
    from app.spatial_planning import near

    a = poi("a", "历史馆")
    b = a.model_copy(update={"coordinate_system": "WGS84"})
    assert near(a, b) == 20000 and distance_meters(a, a) == 0


@pytest.mark.parametrize("keywords", [[""], ["a" * 41]])
def test_profile_exclusions_are_bounded(keywords):
    with pytest.raises(ValueError):
        TravelProfile(excluded_keywords=keywords)


def test_refresh_can_preserve_incrementally_added_trip_place_and_rejects_stale_version(client):
    from app.database import TripRecord

    result = client.post("/api/places/recommendations", json=discovery_body()).json()
    trip = client.post("/api/trips", json=discovery_body(catalog_id=result["catalog_id"])).json()
    added = poi("new", "新查询的历史馆")
    with client.app.state.session_factory.begin() as db:
        saved = db.get(TripRecord, trip["id"])
        saved.catalog = {**saved.catalog, added.id: added.model_dump()}
    body = discovery_body(
        catalog_id=result["catalog_id"], source_trip_id=trip["id"], base_version=0, keep_ids=[added.id]
    )
    response = client.post("/api/places/recommendations", json=body)
    assert response.status_code == 200, response.text
    selected = next(p for p in response.json()["places"] if p["id"] == added.id)
    assert selected["evidence_note"] == added.evidence_note
    assert added.id not in client.get(f"/api/trips/{trip['id']}").json()["requirements"]["must_visit"]
    body["base_version"] = 1
    assert client.post("/api/places/recommendations", json=body).status_code == 409


def test_municipality_district_is_not_filtered_as_a_different_city(monkeypatch):
    tools = TravelTools(Settings(tool_provider="amap", _env_file=None), "上海", adcode="310000")
    monkeypatch.setattr(
        tools,
        "_get",
        lambda *_: {
            "pois": [
                {
                    "id": "museum",
                    "name": "历史馆",
                    "location": "121.4,31.2",
                    "cityname": "上海市",
                    "adcode": "310101",
                    "typecode": "140100",
                },
                {
                    "id": "other",
                    "name": "外市历史馆",
                    "location": "121.4,31.2",
                    "cityname": "上海市",
                    "adcode": "320101",
                    "typecode": "140100",
                },
            ]
        },
    )
    assert set(tools.search_candidates("历史馆", "culture")) == {"amap-museum"}


def test_late_arrival_does_not_receive_an_ordinary_spatial_seed():
    from app.schemas import TravelBoundary

    req = Requirements(
        start_date="2026-10-26",
        days=2,
        lodging_mode="route_first",
        arrival=TravelBoundary(
            location=CATALOG["westlake"].model_dump(
                include={"name", "longitude", "latitude", "coordinate_system"}
            ),
            time="20:00",
        ),
    )
    groups = group_candidates(req, CATALOG, list(CATALOG))
    assert groups[0] == [] and groups[1]


def test_relaxed_pace_does_not_artificially_drop_four_required_stops():
    catalog = {p.id: p for p in [poi(str(i), f"指定历史馆{i}", lon=120.16 + i * 0.001) for i in range(4)]}
    req = Requirements(start_date="2026-10-26", must_visit=list(catalog), lodging_mode="route_first")
    result = generate(req, Settings(_env_file=None), lambda *_: None, catalog=catalog)
    assert {a.place_id for d in result.days for a in d.activities} == set(catalog)
    assert not any(c.severity == "error" for c in result.checks)


def test_nearby_museum_alias_is_not_a_second_ordinary_stop_but_explicit_choices_survive():
    a, b = poi("a", "城市博物馆"), poi("b", "城市博物馆南馆", lon=120.1601)
    catalog = {p.id: p for p in (a, b)}
    assert len(rank_candidates(catalog, TravelProfile())) == 1
    assert len(rank_candidates(catalog, TravelProfile(), protected=[a.id, b.id])) == 2
    far = b.model_copy(update={"longitude": 120.3})
    assert len(rank_candidates({a.id: a, far.id: far}, TravelProfile())) == 2


def test_address_label_and_ticket_counter_are_not_separate_city_activities(monkeypatch):
    tools = TravelTools(Settings(tool_provider="amap", _env_file=None))
    rows = [
        {"id": str(i), "name": name, "location": "120.16,30.25", "typecode": "110200"}
        for i, name in enumerate(("历史街区", "历史街区-中山路47号", "历史街区售票处"))
    ]
    monkeypatch.setattr(tools, "_get", lambda *_: {"pois": rows})
    assert {p.name for p in tools.search_candidates("历史街区", "city").values()} == {"历史街区"}
