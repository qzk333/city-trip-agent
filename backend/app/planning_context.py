"""Small, date-scoped model inputs. Missing evidence remains explicitly unknown."""

from datetime import timedelta

from app.lodging import boundary_window
from app.place_evidence import hard_opening_window

WEATHER_SOURCE = "https://lbs.amap.com/api/webservice/guide/api/weatherinfo"


def weather_context(req, weather):
    payload = weather.model_dump(mode="json") if weather else {}
    requested = [(req.start_date + timedelta(days=i)).isoformat() for i in range(req.days)]
    available = {}
    rows = payload.get("data", {}).get("days", [])
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("date") in requested:
            available[row["date"]] = row
    days = []
    for day in requested:
        row = available.get(day)
        days.append(
            {
                "date": day,
                "status": "forecast" if row else "unknown",
                **(
                    {
                        key: str(row.get(key, ""))[:80]
                        for key in (
                            "dayweather",
                            "nightweather",
                            "daytemp",
                            "nighttemp",
                            "daypower",
                            "nightpower",
                            "reporttime",
                        )
                    }
                    if row
                    else {}
                ),
            }
        )
    return {
        "status": payload.get("status", "unavailable"),
        "provider": payload.get("provider", "unknown"),
        "observed_at": payload.get("observed_at", ""),
        "evidence": payload.get("evidence", "未查询天气"),
        "source_url": WEATHER_SOURCE if payload.get("provider") == "amap" else "",
        "granularity": "day_and_night_not_hourly",
        "days": days,
        "limitation": "只参考接口实际覆盖的日期与白天/夜晚现象，不能推断逐小时降雨、日落或景点是否开放。",
    }


def planning_inputs(req, catalog):
    windows = []
    for day in range(1, req.days + 1):
        start, end = boundary_window(req, day, {})
        windows.append(
            {
                "day": day,
                "earliest": f"{start // 60:02d}:{start % 60:02d}",
                "latest": f"{end // 60:02d}:{end % 60:02d}",
            }
        )
    places = []
    for p in catalog.values():
        shelter = p.traits.indoor_outdoor
        if shelter == "unknown" and p.fixture:
            shelter = "indoor" if p.indoor else "outdoor"
        opening, closing = hard_opening_window(p)
        places.append(
            {
                "id": p.id,
                "name": p.name,
                "category": p.category,
                "longitude": p.longitude,
                "latitude": p.latitude,
                "coordinate_system": p.coordinate_system,
                "estimated_duration_minutes": p.duration_minutes,
                "estimated_duration_range": [p.traits.duration_min, p.traits.duration_max],
                "indoor_outdoor": shelter,
                "shelter_hint": "分类可能适合避雨，实际室内条件待核实"
                if shelter == "unknown" and p.category in {"culture", "coffee"}
                else "实际室内外条件待核实"
                if shelter == "unknown"
                else "演示属性"
                if p.fixture
                else "目录记录，须核对来源",
                "opening": opening,
                "closing": closing,
                "unverified_opening": p.opening if opening is None else None,
                "unverified_closing": p.closing if closing is None else None,
            }
        )
    return {"day_windows": windows, "candidates": places}
