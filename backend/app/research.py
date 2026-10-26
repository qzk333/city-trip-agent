"""Bounded guide retrieval; external page text is evidence, never instructions."""

import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from threading import Lock
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from ddgs import DDGS

from app.config import Settings
from app.schemas import GuideResearch, GuideSource, Requirements
from app.user_config import check_public_dns, validate_public_url

_cache: dict[tuple, tuple[float, GuideResearch]] = {}
_cache_lock = Lock()


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "nav", "footer"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "nav", "footer"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored and data.strip():
            self.parts.append(data.strip())


def public_page_text(url: str, timeout: float = 3) -> str:
    """Pin public DNS and revalidate redirects; no API credentials are forwarded."""
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        for _ in range(3):
            parsed = urlsplit(url)
            validate_public_url(urlunsplit(parsed._replace(query="")))
            addresses = check_public_dns(url)
            original = httpx.URL(url)
            host = original.raw_host.decode("ascii")
            endpoint = original.copy_with(host=addresses[0])
            request = client.build_request(
                "GET",
                endpoint,
                headers={"Host": host, "User-Agent": "CityTripResearch/0.1"},
                extensions={"sni_hostname": host},
            )
            response = client.send(request, stream=True)
            try:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", ""):
                    return ""
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    body.extend(chunk)
                    if len(body) >= 262144:
                        break
                parser = PageText()
                parser.feed(bytes(body).decode(response.encoding or "utf-8", errors="replace"))
                return re.sub(r"\s+", " ", " ".join(parser.parts))[:7000]
            finally:
                response.close()
    return ""


def search_guides(query: str, timeout: float) -> list[dict]:
    return DDGS(timeout=timeout).text(query, region="cn-zh", max_results=6, backend="bing")


def research_guides(req: Requirements, settings: Settings, deadline: float) -> GuideResearch:
    if not req.use_web_guides or (settings.planning_mode == "demo" and settings.tool_provider == "demo"):
        return GuideResearch(note="本次未联网检索攻略；演示模式不会声称已参考网上资料。")
    key = (req.country_code, req.city, req.days)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] > monotonic():
            return cached[1].model_copy(deep=True)
    query = f"{req.city} {req.days}天 旅游攻略 行程 交通 步行 打车"
    result = GuideResearch(query=query, fetched_at=datetime.now(UTC).isoformat())
    try:
        timeout = min(settings.guide_search_timeout_seconds, max(1, deadline - monotonic()))
        rows = search_guides(query, timeout)
        urls = set()
        for row in rows:
            url = row.get("href", "")
            parsed = urlsplit(url)
            title, excerpt = row.get("title", ""), row.get("body", "")
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or url in urls:
                continue
            try:
                validate_public_url(urlunsplit(parsed._replace(scheme="https", query="")))
            except ValueError:
                continue
            if req.city not in title + excerpt:
                continue
            urls.add(url)
            source = GuideSource(
                id=f"guide-{len(result.sources) + 1}", title=title[:200], url=url, excerpt=excerpt[:700]
            )
            # Fetch at most two public HTTPS pages. Failed/login-only pages remain search excerpts.
            if len(result.sources) < 2 and parsed.scheme == "https" and deadline - monotonic() > 6:
                try:
                    content = public_page_text(url)
                    if req.city in content and len(content) > 200:
                        source.excerpt = (excerpt + " " + content)[:2400]
                        source.content_kind = "page_excerpt"
                except (httpx.HTTPError, ValueError, OSError):
                    pass
            result.sources.append(source)
        result.status = (
            "ok"
            if any(source.content_kind == "page_excerpt" for source in result.sources)
            else "partial"
            if result.sources
            else "unavailable"
        )
        result.note = (
            "已检索公开攻略，交通建议为参考；搜索摘要与已读取的页面片段分别标注，营业及班次另行确认。"
            if result.sources
            else "没有检索到与当前城市相关的可用攻略；本次按地点距离安排，未虚构来源。"
        )
    except Exception:
        result.status = "unavailable"
        result.note = "攻略检索暂时不可用；本次按地点距离安排，未将模型常识冒充联网结果。"
    with _cache_lock:
        if len(_cache) > 100:
            _cache.clear()
        _cache[key] = (monotonic() + (1800 if result.sources else 30), result.model_copy(deep=True))
    return result
