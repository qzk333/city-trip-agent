import socket

import httpx
import pytest

from app.user_config import public_model_post


def test_user_endpoint_is_pinned_to_validated_ip_with_original_tls_host(monkeypatch):
    monkeypatch.setattr("app.user_config.check_public_dns", lambda _: ["93.184.216.34"])
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": []})

    actual_client = httpx.Client
    monkeypatch.setattr(
        "app.user_config.httpx.Client",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(handle),
            **kwargs,
        ),
    )
    public_model_post(
        "https://model.example/v1/chat/completions",
        headers={"Authorization": "Bearer own-key"},
        json={"model": "example"},
        timeout=5,
    )
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["Host"] == "model.example"
    assert seen[0].extensions["sni_hostname"] == "model.example"
    assert seen[0].headers["Authorization"] == "Bearer own-key"


def test_dns_pointing_to_private_service_is_rejected_before_http(monkeypatch):
    monkeypatch.setattr(
        "app.user_config.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError, match="公开网络"):
        public_model_post("https://model.example/v1/chat/completions", headers={}, json={}, timeout=5)


def test_redirects_are_not_followed_with_user_credentials(monkeypatch):
    monkeypatch.setattr("app.user_config.check_public_dns", lambda _: ["93.184.216.34"])
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})

    actual_client = httpx.Client
    monkeypatch.setattr(
        "app.user_config.httpx.Client",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(handle),
            **kwargs,
        ),
    )
    result = public_model_post(
        "https://model.example/v1/chat/completions", headers={"Authorization": "own-key"}, json={}, timeout=5
    )
    assert result.status_code == 302
    assert len(seen) == 1
