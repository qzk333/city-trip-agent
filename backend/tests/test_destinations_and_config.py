import json
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from app.catalog import CATALOG
from app.config import Settings
from app.database import CatalogRecord, TripRecord
from app.geography import get_city
from app.main import create_app


def headers(**config):
    return {"x-travel-config": quote(json.dumps(config))}


def test_hierarchical_directory_has_countries_states_and_cities(client):
    countries = client.get("/api/geography/countries").json()
    assert len(countries) >= 200
    states = client.get("/api/geography/states?country_code=CN").json()
    assert any(s["id"] == "CN-ZJ" for s in states)
    cities = client.get("/api/geography/cities?state_id=CN-ZJ&q=杭州").json()
    assert any(c["id"] == 19598 for c in cities["items"])
    assert client.get("/api/geography/cities/-310000").json()["name"] == "上海"
    japan = client.get("/api/geography/cities?state_id=JP-13&q=Tokyo").json()
    assert any(c["id"] == 64500 for c in japan["items"])


def test_guangdong_contains_exactly_21_chinese_prefecture_cities(client):
    result = client.get("/api/geography/cities?state_id=CN-GD").json()
    expected = {
        "广州",
        "深圳",
        "珠海",
        "汕头",
        "佛山",
        "韶关",
        "河源",
        "梅州",
        "惠州",
        "汕尾",
        "东莞",
        "中山",
        "江门",
        "阳江",
        "湛江",
        "茂名",
        "肇庆",
        "清远",
        "潮州",
        "揭阳",
        "云浮",
    }
    assert result["total"] == 21
    assert {city["name"] for city in result["items"]} == expected
    foshan = client.get("/api/geography/cities?state_id=CN-GD&q=佛山").json()["items"]
    assert len(foshan) == 1
    assert foshan[0]["id"] == 19514  # Preserve the original city ID for saved trips.
    assert client.get("/api/geography/cities?state_id=CN-GD&q=Foshan").json()["items"] == foshan


@pytest.mark.parametrize("state_id,count", [("CN-ZJ", 11), ("CN-JS", 13), ("CN-SC", 21), ("CN-TJ", 1)])
def test_mainland_dropdown_uses_city_level_not_districts(client, state_id, count):
    result = client.get(f"/api/geography/cities?state_id={state_id}").json()
    assert result["total"] == count
    assert all(any("\u4e00" <= char <= "\u9fff" for char in city["name"]) for city in result["items"])


def test_guangdong_city_can_generate_a_city_scoped_itinerary(client):
    response = client.post(
        "/api/trips",
        json={"start_date": "2026-10-24", "city_id": 19514, "country_code": "CN", "state_id": "CN-GD"},
    )
    assert response.status_code == 201, response.text
    trip = response.json()
    assert trip["requirements"]["city"] == "佛山"
    assert all("佛山" in place["name"] for place in trip["places"])
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"


def test_selected_city_drives_catalog_planning_and_saved_snapshot(client):
    created = client.post(
        "/api/trips",
        json={"start_date": "2026-10-24", "city_id": -310000, "country_code": "CN", "state_id": "CN-SH"},
    )
    assert created.status_code == 201, created.text
    trip = created.json()
    assert trip["requirements"]["city"] == "上海"
    assert trip["requirements"]["hotel"]["longitude"] > 121
    assert all("上海" in p["name"] for p in trip["places"])
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed", run
    itinerary = run["proposal"]["itinerary"]
    assert itinerary["status"] == "NEEDS_CONFIRMATION"
    assert all(a["place_id"].startswith("demo--310000-") for d in itinerary["days"] for a in d["activities"])
    assert client.get("/api/places").json()[0]["id"] == "westlake"
    assert client.get(f"/api/trips/{trip['id']}").json()["places"] == trip["places"]


def test_foreign_city_uses_its_own_timezone_and_not_hangzhou_places(client):
    response = client.post(
        "/api/trips",
        json={"start_date": "2026-10-24", "city_id": 64500, "country_code": "JP", "state_id": "JP-13"},
    )
    assert response.status_code == 201
    assert response.json()["requirements"]["timezone"] == "Asia/Tokyo"
    assert all(p["id"] not in CATALOG for p in response.json()["places"])


def test_mismatched_destination_or_other_city_place_is_rejected(client):
    invalid = {"start_date": "2026-10-24", "city_id": 19598, "country_code": "JP", "state_id": "JP-13"}
    assert client.post("/api/trips", json=invalid).status_code == 422
    invalid.update(
        {"city_id": -310000, "country_code": "CN", "state_id": "CN-SH", "must_visit": ["westlake"]}
    )
    assert client.post("/api/trips", json=invalid).status_code == 422


def test_catalog_discovery_is_scoped_to_the_selected_city(client, monkeypatch):
    poi = CATALOG["westlake"].model_copy(update={"id": "amap-test", "fixture": False})
    monkeypatch.setattr(
        "app.tools.TravelTools.discover",
        lambda _, city: {
            "places": {poi.id: poi.model_dump()},
            "center": {
                "name": "参考起点",
                "longitude": 120.15,
                "latitude": 30.25,
                "coordinate_system": "GCJ-02",
            },
        },
    )
    result = client.post(
        "/api/places/discover",
        json={"city_id": 19598},
        headers=headers(tool_provider="amap", amap_api_key="browser-key"),
    ).json()
    body = {
        "start_date": "2026-10-24",
        "city_id": 19598,
        "catalog_id": result["catalog_id"],
        "must_visit": ["amap-test"],
    }
    assert client.post("/api/trips", json=body).status_code == 201
    body.update({"city_id": -310000, "state_id": "CN-SH"})
    assert client.post("/api/trips", json=body).status_code == 422
    with client.app.state.session_factory() as db:
        assert "browser-key" not in json.dumps(db.get(CatalogRecord, result["catalog_id"]).places)


def test_web_requests_never_inherit_owner_credentials(tmp_path, monkeypatch):
    base = Settings(
        database_url=f"sqlite:///{(tmp_path / 'owner.sqlite3').as_posix()}",
        tool_provider="amap",
        planning_mode="llm",
        amap_api_key="owner-map-key",
        llm_api_key="owner-model-key",
        llm_base_url="https://owner.example/v1",
        llm_model="owner-model",
        _env_file=None,
    )
    captured = []
    monkeypatch.setattr("app.main.execute_run", lambda _, runtime, *args: captured.append(runtime))
    with TestClient(create_app(base)) as client:
        default = client.get("/api/health").json()
        assert default["tool_provider"] == "demo" and default["planning_mode"] == "demo"
        assert default["llm_configured"] is False
        trip = client.post("/api/trips", json={"start_date": "2026-10-24"}).json()
        for key in ("visitor-a-key", "visitor-b-key"):
            assert (
                client.post(
                    f"/api/trips/{trip['id']}/plan-runs",
                    headers=headers(
                        tool_provider="amap",
                        amap_api_key=key,
                    ),
                ).status_code
                == 202
            )
        assert [s.amap_api_key for s in captured] == ["visitor-a-key", "visitor-b-key"]
        assert all(s.llm_api_key == "" for s in captured)
        assert base.amap_api_key == "owner-map-key"
        payload = client.get(f"/api/trips/{trip['id']}").text
        assert "visitor-a-key" not in payload and "owner-map-key" not in payload
        with client.app.state.session_factory() as db:
            assert "visitor" not in json.dumps(db.get(TripRecord, trip["id"]).requirements)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/v1",
        "https://user:password@example.com/v1",
        "https://example.com/v1?key=secret",
    ],
)
def test_invalid_user_model_addresses_do_not_echo_keys(client, url):
    response = client.get(
        "/api/health",
        headers=headers(
            planning_mode="llm", llm_base_url=url, llm_model="model", llm_api_key="private-user-secret"
        ),
    )
    assert response.status_code == 400
    assert "private-user-secret" not in response.text


def test_connection_test_passes_only_user_key_and_sanitizes_upstream_errors(client, monkeypatch):
    monkeypatch.setattr("app.user_config.check_public_dns", lambda _: None)
    captured = []

    def post(url, **kwargs):
        captured.append(kwargs["headers"]["Authorization"])
        return httpx.Response(
            401, json={"error": "echo-private-user-key"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.user_config.public_model_post", post)
    response = client.post(
        "/api/settings/test",
        json={"service": "model"},
        headers=headers(
            planning_mode="llm",
            llm_base_url="https://public.example/v1",
            llm_model="model",
            llm_api_key="echo-private-user-key",
        ),
    )
    assert response.status_code == 400
    assert captured == ["Bearer echo-private-user-key"]
    assert "echo-private-user-key" not in response.text


@pytest.mark.parametrize(
    "status,fragment", [(401, "认证失败"), (403, "认证失败"), (404, "404"), (429, "429"), (503, "5xx")]
)
def test_model_connection_reports_actionable_http_errors_without_upstream_secrets(
    client, monkeypatch, status, fragment
):
    def post(url, **kwargs):
        return httpx.Response(status, json={"error": "do-not-echo-key"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.user_config.public_model_post", post)
    response = client.post(
        "/api/settings/test",
        json={"service": "model"},
        headers=headers(
            planning_mode="llm",
            llm_base_url="https://model.example/v1",
            llm_model="example",
            llm_api_key="do-not-echo-key",
        ),
    )
    assert response.status_code == 400
    assert fragment in response.json()["detail"]
    assert "do-not-echo-key" not in response.text


@pytest.mark.parametrize(
    "exception,fragment", [(httpx.ReadTimeout, "超时"), (httpx.ConnectError, "无法连接")]
)
def test_model_connection_network_failure_has_visible_safe_message(client, monkeypatch, exception, fragment):
    def post(*args, **kwargs):
        raise exception("do-not-echo-key")

    monkeypatch.setattr("app.user_config.public_model_post", post)
    response = client.post(
        "/api/settings/test",
        json={"service": "model"},
        headers=headers(
            planning_mode="llm",
            llm_base_url="https://model.example/v1",
            llm_model="example",
            llm_api_key="do-not-echo-key",
        ),
    )
    assert response.status_code == 400
    assert fragment in response.json()["detail"]
    assert "do-not-echo-key" not in response.text


@pytest.mark.parametrize(
    "payload,ok", [({"choices": [{"message": {"content": "OK"}}]}, True), ([], False), ({}, False)]
)
def test_model_connection_success_and_incompatible_response(client, monkeypatch, payload, ok):
    def post(url, **kwargs):
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.user_config.public_model_post", post)
    response = client.post(
        "/api/settings/test",
        json={"service": "model"},
        headers=headers(
            planning_mode="llm",
            llm_base_url="https://model.example/v1",
            llm_model="example",
            llm_api_key="do-not-echo-key",
        ),
    )
    assert response.status_code == (200 if ok else 400)
    assert "do-not-echo-key" not in response.text


def test_deepseek_connection_probe_uses_the_same_non_thinking_mode_as_planning(client, monkeypatch):
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.user_config.public_model_post", post)
    response = client.post(
        "/api/settings/test",
        json={"service": "model"},
        headers=headers(
            planning_mode="llm",
            llm_base_url="https://api.deepseek.com",
            llm_model="example-model",
            llm_api_key="fixture-key",
        ),
    )
    assert response.status_code == 200
    assert requests[0]["thinking"] == {"type": "disabled"}


def test_unknown_city_is_not_silently_replaced(client):
    assert client.get("/api/geography/cities/99999999").status_code == 404
    assert get_city(19598)["name"] == "杭州"
