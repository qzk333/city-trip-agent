import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.amap_gateway import AmapRequestError, amap_get
from app.config import Settings


def response(data, status=200):
    return httpx.Response(status, json=data, request=httpx.Request("GET", "https://restapi.amap.com/test"))


def test_same_key_serializes_network_requests_across_runs():
    settings = Settings(amap_api_key="test-serialization", amap_min_interval_seconds=0, _env_file=None)
    active = 0
    maximum = 0
    guard = threading.Lock()

    def request(*args, **kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.015)
        with guard:
            active -= 1
        return response({"status": "1"})

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(amap_get, settings, "/test", {"index": i}, time.monotonic() + 5, request)
            for i in range(4)
        ]
        assert all(future.result()[0]["status"] == "1" for future in futures)
    assert maximum == 1


def test_spacing_retries_and_cache_are_shared_for_a_key(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.amap_gateway.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.amap_gateway.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    settings = Settings(amap_api_key="test-spacing", amap_min_interval_seconds=1.05, _env_file=None)
    starts = []

    def request(*args, **kwargs):
        starts.append(clock[0])
        return response(
            {"status": "0", "infocode": "10021"} if len(starts) < 3 else {"status": "1", "route": {}}
        )

    data, cached, retries = amap_get(settings, "/test", {"q": "city"}, 120, request)
    assert data["status"] == "1" and not cached and retries == 2
    assert all(second - first >= 1.04 for first, second in zip(starts, starts[1:], strict=False))
    assert amap_get(settings, "/test", {"q": "city"}, 120, request)[1] is True
    assert len(starts) == 3


def test_daily_quota_is_not_retried_or_reported_as_temporary_qps():
    settings = Settings(amap_api_key="do-not-echo", _env_file=None)
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        return response({"status": "0", "infocode": "10003", "info": "do-not-echo"})

    with pytest.raises(AmapRequestError) as caught:
        amap_get(settings, "/test", {}, time.monotonic() + 5, request)
    assert len(calls) == 1 and caught.value.fatal
    assert "日调用额度" in str(caught.value) and "do-not-echo" not in str(caught.value)


def test_qps_failure_exits_after_bounded_retries(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.amap_gateway.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.amap_gateway.sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    settings = Settings(amap_api_key="test-exhausted", _env_file=None)
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        return response({}, 429)

    with pytest.raises(AmapRequestError, match="QPS"):
        amap_get(settings, "/test", {}, 130, request)
    assert len(calls) == 3
