import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CONFIG = {
    "planning_mode": "llm",
    "tool_provider": "amap",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-flash",
    "llm_api_key": "local-model-fixture",
    "amap_api_key": "local-map-fixture",
    "amap_js_key": "local-js-fixture",
    "amap_security_code": "local-security-fixture",
}


@pytest.fixture
def local_settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'local.sqlite3').as_posix()}",
        local_api_config_path=tmp_path / ".local/api-config.json",
        _env_file=None,
    )


def local_client(settings, **kwargs):
    return TestClient(
        create_app(settings), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000), **kwargs
    )


def test_project_config_persists_keys_and_survives_application_restart(local_settings):
    with local_client(local_settings) as client:
        assert client.get("/api/settings/local").json()["exists"] is False
        response = client.post("/api/settings/local", json=CONFIG)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert json.loads(local_settings.local_api_config_path.read_text(encoding="utf-8")) == CONFIG
        assert list(local_settings.local_api_config_path.parent.glob("*.tmp")) == []
    with local_client(local_settings) as client:
        response = client.get("/api/settings/local")
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["config"] == CONFIG
        health = client.get("/api/health").json()
        assert health["planning_mode"] == "llm" and health["tool_provider"] == "amap"
        assert health["llm_configured"] is True
        assert "local-model-fixture" not in json.dumps(health)


def test_clearing_removes_file_and_returns_demo_defaults(local_settings):
    with local_client(local_settings) as client:
        client.post("/api/settings/local", json=CONFIG)
        assert client.post("/api/settings/local/clear", json={}).status_code == 200
        assert not local_settings.local_api_config_path.exists()
        assert client.get("/api/health").json()["planning_mode"] == "demo"
        assert client.post("/api/settings/local/clear", json={}).status_code == 200


@pytest.mark.parametrize("data", [{**CONFIG, "llm_base_url": "http://127.0.0.1/private"}, [CONFIG]])
def test_invalid_save_does_not_overwrite_or_echo_keys(local_settings, data):
    with local_client(local_settings) as client:
        client.post("/api/settings/local", json=CONFIG)
        previous = local_settings.local_api_config_path.read_bytes()
        response = client.post("/api/settings/local", json=data)
        assert response.status_code == 400
        assert "local-model-fixture" not in response.text
        assert local_settings.local_api_config_path.read_bytes() == previous


def test_failed_atomic_write_preserves_previous_config(local_settings, monkeypatch):
    with local_client(local_settings) as client:
        client.post("/api/settings/local", json=CONFIG)
        previous = local_settings.local_api_config_path.read_bytes()

        def fail(*args):
            raise OSError("local-model-fixture")

        monkeypatch.setattr("app.local_config.os.replace", fail)
        response = client.post("/api/settings/local", json={**CONFIG, "llm_api_key": "new-fixture"})
        assert response.status_code == 500
        assert "local-model-fixture" not in response.text
        assert local_settings.local_api_config_path.read_bytes() == previous
        assert list(local_settings.local_api_config_path.parent.glob("*.tmp")) == []


def test_corrupt_file_is_reported_without_erasing_it_or_echoing_secrets(local_settings):
    path = local_settings.local_api_config_path
    path.parent.mkdir(parents=True)
    path.write_text('{"llm_api_key":"private-incomplete', encoding="utf-8")
    with local_client(local_settings) as client:
        response = client.get("/api/settings/local")
        assert response.status_code == 500
        assert "private-incomplete" not in response.text
        assert "private-incomplete" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("peer,host", [("203.0.113.10", "127.0.0.1"), ("127.0.0.1", "foreign.example")])
def test_remote_request_cannot_read_change_or_inherit_project_credentials(local_settings, peer, host):
    with local_client(local_settings) as client:
        client.post("/api/settings/local", json=CONFIG)
    with TestClient(create_app(local_settings), base_url=f"http://{host}", client=(peer, 50000)) as client:
        assert client.get("/api/settings/local").status_code == 403
        assert client.post("/api/settings/local", json=CONFIG).status_code == 403
        assert client.post("/api/settings/local/clear", json={}).status_code == 403
        assert client.get("/api/health").json()["llm_configured"] is False
    assert json.loads(local_settings.local_api_config_path.read_text(encoding="utf-8")) == CONFIG


def test_cross_site_origin_cannot_read_or_modify_local_config(local_settings):
    with local_client(local_settings) as client:
        client.post("/api/settings/local", json=CONFIG)
        assert (
            client.get("/api/health", headers={"Origin": "https://foreign.example"}).json()["llm_configured"]
            is False
        )
        for method, path in [
            ("GET", "/api/settings/local"),
            ("POST", "/api/settings/local"),
            ("POST", "/api/settings/local/clear"),
        ]:
            response = client.request(
                method, path, json=CONFIG, headers={"Origin": "https://foreign.example"}
            )
            assert response.status_code == 403
    assert json.loads(local_settings.local_api_config_path.read_text(encoding="utf-8")) == CONFIG
