import ipaddress
import json
import socket
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import HTTPException, Request
from pydantic import Field, SecretStr, ValidationError, model_validator

from app.config import Settings
from app.schemas import Schema


class UserAPIConfig(Schema):
    tool_provider: str = Field(default="demo", pattern="^(demo|amap)$")
    planning_mode: str = Field(default="demo", pattern="^(demo|llm)$")
    amap_api_key: SecretStr = Field(default=SecretStr(""), max_length=2048)
    llm_base_url: str = Field(default="", max_length=2048)
    llm_api_key: SecretStr = Field(default=SecretStr(""), max_length=2048)
    llm_model: str = Field(default="", max_length=150)

    @model_validator(mode="after")
    def valid_modes(self):
        if self.tool_provider == "amap" and not self.amap_api_key.get_secret_value().strip():
            raise ValueError("选择真实地图后，请填写自己的高德 Web 服务 Key")
        if self.planning_mode == "llm":
            if not (self.llm_model.strip() and self.llm_api_key.get_secret_value().strip()):
                raise ValueError("请填写模型名称和自己的模型 API Key")
            validate_public_url(self.llm_base_url)
        return self


def validate_public_url(url: str):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
    except ValueError as exc:
        raise ValueError("模型地址须为公开 HTTPS 地址，不含账号、查询参数或内网地址") from exc


def check_public_dns(url: str) -> list[str]:
    host = urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError
        return list(dict.fromkeys(item[4][0] for item in addresses))
    except (OSError, ValueError) as exc:
        raise ValueError("模型服务地址无法解析为公开网络地址") from exc


def public_model_post(url: str, *, headers: dict, json: dict, timeout: float) -> httpx.Response:
    """Pin the validated public IP, preserving TLS SNI and Host; never follow redirects."""
    validate_public_url(url)
    addresses = check_public_dns(url)
    original = httpx.URL(url)
    host = original.raw_host.decode("ascii")
    endpoint = original.copy_with(host=addresses[0])
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        outbound = client.build_request(
            "POST", endpoint, json=json, headers={**headers, "Host": host}, extensions={"sni_hostname": host}
        )
        return client.send(outbound)


def request_settings(base: Settings, request: Request) -> Settings:
    raw = request.headers.get("x-travel-config", "")
    if len(raw) > 20000:
        raise HTTPException(400, "API 配置过长，请检查填写内容")
    try:
        config = UserAPIConfig.model_validate(json.loads(unquote(raw))) if raw else UserAPIConfig()
    except (ValueError, ValidationError, TypeError) as exc:
        # Never echo validation inputs: they may contain private keys.
        raise HTTPException(400, "API 配置无效，请检查模式、Key 和公开 HTTPS 模型地址") from exc
    return base.model_copy(
        update={
            "tool_provider": config.tool_provider,
            "planning_mode": config.planning_mode,
            "amap_api_key": config.amap_api_key.get_secret_value().strip(),
            "llm_base_url": config.llm_base_url.strip().rstrip("/"),
            "llm_api_key": config.llm_api_key.get_secret_value().strip(),
            "llm_model": config.llm_model.strip(),
            "user_supplied_credentials": True,
        }
    )


def test_model_connection(settings: Settings) -> dict:
    if settings.planning_mode != "llm":
        raise HTTPException(400, "请先选择使用自己的模型")
    try:
        response = public_model_post(
            settings.llm_base_url + "/chat/completions",
            timeout=12,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 16,
            },
        )
        if response.status_code in (401, 403):
            raise HTTPException(400, "模型认证失败，请检查自己的 Key 和访问权限")
        response.raise_for_status()
        data = response.json()
        if not data.get("choices"):
            raise ValueError
        return {"ok": True, "message": "模型已响应；工具调用兼容性会在规划时进一步检查。"}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(400, "模型连接失败，请检查地址、模型名称和服务可用性") from exc
