"""Explicitly test configured providers without printing credentials or raw responses."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.catalog import CATALOG
from app.config import get_settings
from app.llm import model_candidates
from app.tools import ToolUnavailable, TravelTools

settings = get_settings()
tools = TravelTools(settings)
today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
results = []
if settings.tool_provider == "amap" and settings.amap_api_key:
    for origin, destination in [
        ("westlake", "gushan_museum"),
        ("gushan_museum", "hefang"),
        ("hefang", "art_museum"),
    ]:
        for mode in ("walking", "transit"):
            try:
                route = tools.route(
                    origin, CATALOG[origin], destination, CATALOG[destination], mode
                )
                results.append(
                    {
                        "tool": "get_routes",
                        "status": "ok",
                        "mode": mode,
                        "minutes": route.duration_minutes,
                    }
                )
            except ToolUnavailable as exc:
                results.append(
                    {"tool": "get_routes", "status": "unavailable", "error": str(exc)}
                )
    for day in (today, date(2030, 1, 1)):
        result = tools.weather(day, 1)
        results.append(
            {"tool": "get_weather", "date": day.isoformat(), "status": result.status}
        )
else:
    results.append(
        {"provider": "amap", "status": "SKIPPED", "reason": "未配置真实工具模式与 Key"}
    )

if settings.planning_mode == "llm" and all(
    [settings.llm_base_url, settings.llm_api_key, settings.llm_model]
):
    from app.schemas import Requirements

    try:
        trace = []
        order = model_candidates(
            Requirements(start_date=today), settings, trace, lambda *_: None
        )
        results.append(
            {
                "provider": "llm",
                "status": "ok",
                "candidate_count": len(order),
                "tool_call_count": sum(
                    t["tool"] in ("search_places", "lookup_place_notes") for t in trace
                ),
            }
        )
    except ToolUnavailable as exc:
        results.append({"provider": "llm", "status": "unavailable", "error": str(exc)})
else:
    results.append(
        {"provider": "llm", "status": "SKIPPED", "reason": "未配置真实模型模式与凭据"}
    )
print(json.dumps(results, ensure_ascii=False, indent=2))
raise SystemExit(1 if any(r["status"] == "unavailable" for r in results) else 0)
