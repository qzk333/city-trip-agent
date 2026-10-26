"""Per-key serialization, bounded QPS retries and a short shared response cache."""

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic, sleep

import httpx

from app.config import Settings


class AmapRequestError(Exception):
    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


@dataclass
class KeyState:
    lock: Lock = field(default_factory=Lock)
    next_start: float = 0
    cache: OrderedDict = field(default_factory=OrderedDict)


_states: dict[str, KeyState] = {}
_registry_lock = Lock()
RETRY_CODES = {"10014", "10015", "10016", "10019", "10020", "10021", "10029"}


def amap_get(
    settings: Settings, path: str, params: dict, deadline: float, request=httpx.get
) -> tuple[dict, bool, int]:
    fingerprint = hashlib.sha256(settings.amap_api_key.encode()).hexdigest()
    with _registry_lock:
        state = _states.setdefault(fingerprint, KeyState())
    cache_key = (path, json.dumps(params, sort_keys=True, separators=(",", ":")))
    if not state.lock.acquire(timeout=max(0, deadline - monotonic())):
        raise AmapRequestError("等待高德请求队列超时，请稍后重试")
    try:
        cached = state.cache.get(cache_key)
        if cached and cached[0] > monotonic():
            state.cache.move_to_end(cache_key)
            return json.loads(cached[1]), True, 0
        for attempt in range(settings.amap_max_retries + 1):
            wait = max(0, state.next_start - monotonic())
            if monotonic() + wait >= deadline:
                raise AmapRequestError("高德请求队列已达到本次时间预算")
            if wait:
                sleep(wait)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AmapRequestError("高德请求已达到本次时间预算")
            state.next_start = monotonic() + settings.amap_min_interval_seconds
            response = request(
                f"https://restapi.amap.com{path}",
                params={**params, "key": settings.amap_api_key},
                timeout=min(settings.tool_timeout_seconds, remaining),
            )
            retry = response.status_code == 429 or response.status_code in (502, 503, 504)
            if not retry:
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise AmapRequestError("高德响应格式无效")
                code = str(data.get("infocode", ""))
                retry = code in RETRY_CODES
                if not retry:
                    if data.get("status") != "1":
                        if code in {"10003", "10044"}:
                            raise AmapRequestError("高德日调用额度已用尽，请检查控制台额度", fatal=True)
                        raise AmapRequestError("高德拒绝请求，请检查 Key、接口权限与账号配置", fatal=True)
                    state.cache[cache_key] = (monotonic() + 300, json.dumps(data))
                    state.cache.move_to_end(cache_key)
                    while len(state.cache) > 256:
                        state.cache.popitem(last=False)
                    return data, False, attempt
            if attempt == settings.amap_max_retries:
                raise AmapRequestError("高德并发或 QPS 限制，排队重试后仍不可用，请稍后重试")
            state.next_start = max(state.next_start, monotonic() + 0.8 * 2**attempt)
        raise AmapRequestError("高德暂时不可用")
    except (httpx.HTTPError, ValueError) as exc:
        raise AmapRequestError("高德网络请求失败，请稍后重试") from exc
    finally:
        state.lock.release()
