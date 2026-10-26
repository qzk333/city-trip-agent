"""Project-local credentials for the personal, loopback-only workbench."""

import ipaddress
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from pydantic import Field, SecretStr

from app.config import Settings
from app.user_config import UserAPIConfig

_lock = RLock()


class ProjectAPIConfig(UserAPIConfig):
    amap_js_key: SecretStr = Field(default=SecretStr(""), max_length=2048)
    amap_security_code: SecretStr = Field(default=SecretStr(""), max_length=2048)


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def is_local_request(request: Request) -> bool:
    if not (request.client and is_loopback(request.client.host) and is_loopback(request.url.hostname)):
        return False
    origin = request.headers.get("origin")
    try:
        return not origin or is_loopback(urlsplit(origin).hostname)
    except ValueError:
        return False


def require_local_request(request: Request):
    if not is_local_request(request):
        raise HTTPException(403, "项目 API 配置仅允许本机访问")


def plain_config(config: ProjectAPIConfig) -> dict:
    return {
        key: value.get_secret_value() if isinstance(value, SecretStr) else value
        for key, value in config.model_dump().items()
    }


def read_config(settings: Settings) -> ProjectAPIConfig:
    with _lock:
        path = settings.local_api_config_path
        if not path.exists():
            return ProjectAPIConfig()
        try:
            return ProjectAPIConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise HTTPException(
                500, "项目 API 配置文件无法读取或格式无效，请检查本地文件；原文件未被覆盖"
            ) from exc


def config_view(settings: Settings) -> dict:
    with _lock:
        config = read_config(settings)
        return {
            "config": plain_config(config),
            "exists": settings.local_api_config_path.exists(),
            "path": str(settings.local_api_config_path),
        }


def write_config(settings: Settings, data) -> dict:
    try:
        config = ProjectAPIConfig.model_validate(data)
    except ValueError as exc:
        raise HTTPException(400, "API 配置无效，请检查模式、Key 和公开 HTTPS 模型地址") from exc
    path = settings.local_api_config_path
    temporary: Path | None = None
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(plain_config(config), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        except OSError as exc:
            raise HTTPException(500, "无法保存项目 API 配置，请检查本地目录写入权限；原配置未被覆盖") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return config_view(settings)


def clear_config(settings: Settings) -> dict:
    with _lock:
        try:
            settings.local_api_config_path.unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(500, "无法清除项目 API 配置，请检查本地文件权限") from exc
        return config_view(settings)
