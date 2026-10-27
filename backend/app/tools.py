import math
import re
from datetime import UTC, date, datetime
from time import monotonic

import httpx

from app.amap_gateway import AmapRequestError, amap_get
from app.config import Settings
from app.place_evidence import provider_place
from app.schemas import GuideResearch, Location, Place, RouteLeg, ToolResult


class ToolUnavailable(Exception):
    def __init__(self, message: str, *, fatal: bool = False, code: str = "tool_unavailable", details=None):
        super().__init__(message)
        self.fatal = fatal
        self.code = code
        self.details = details or []


def distance_meters(a: Location | Place, b: Location | Place) -> int:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat, dlon = lat2 - lat1, math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(6371000 * 2 * math.asin(min(1, math.sqrt(h))))


class TravelTools:
    """One instance per run: bounded calls and a run-scoped route cache."""

    def __init__(
        self,
        settings: Settings,
        city: str = "杭州",
        country_code: str = "CN",
        adcode: str | None = None,
        province: str = "",
    ):
        self.settings = settings
        self.city = city
        self.country_code = country_code
        self.adcode = adcode
        self.city_query = province + city
        self.cache: dict[tuple, RouteLeg] = {}
        self.route_calls = 0
        self.converted_coordinates: dict[tuple, tuple[float, float]] = {}
        self.trace: list[dict] = []
        self.deadline = monotonic() + settings.max_run_seconds
        self.guides = GuideResearch()

    def remaining_seconds(self) -> float:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise ToolUnavailable("已达到本次规划时间预算，请减少候选或稍后重试")
        return remaining

    def _get(self, path: str, params: dict) -> dict:
        if not self.settings.amap_api_key:
            raise ToolUnavailable("高德 Key 尚未配置，请在网页 API 设置中填写自己的 Key")
        if self.country_code != "CN":
            raise ToolUnavailable("当前接入的是高德中国大陆标准服务，境外目的地请使用演示模式")
        try:
            data, cached, retries = amap_get(self.settings, path, params, self.deadline, request=httpx.get)
            if cached or retries:
                self.trace.append(
                    {
                        "tool": "amap_request",
                        "status": "cache_hit" if cached else "retried",
                        "retries": retries,
                    }
                )
            return data
        except AmapRequestError as exc:
            raise ToolUnavailable(str(exc), fatal=exc.fatal) from exc

    def route(
        self, origin_id: str, a: Location | Place, destination_id: str, b: Location | Place, mode: str
    ) -> RouteLeg:
        if mode == "auto":
            return self._auto_route(origin_id, a, destination_id, b)
        key = (origin_id, destination_id, mode)
        self.remaining_seconds()
        if key in self.cache:
            return self.cache[key]
        if self.route_calls >= self.settings.max_route_calls:
            raise ToolUnavailable("已达到本次路线查询预算，未完成全部路线检查")
        self.route_calls += 1
        if self.settings.tool_provider == "demo":
            distance = max(150, round(distance_meters(a, b) * 1.3))
            walking = distance if mode == "walking" or distance < 900 else min(900, distance // 3)
            minutes = (
                max(5, math.ceil(distance / 65))
                if mode == "walking"
                else max(
                    8,
                    math.ceil(distance / 210) + 10,
                )
            )
            leg = RouteLeg(
                origin_id=origin_id,
                destination_id=destination_id,
                duration_minutes=minutes,
                distance_meters=distance,
                walking_meters=walking,
                provider="demo",
                evidence="自建演示估算：按直线距离和固定速度计算，不是真实导航路线。",
                mode=mode,
            )
        else:
            if a.coordinate_system == "WGS84" or b.coordinate_system == "WGS84":
                a, b = self._to_amap(a), self._to_amap(b)
            params = {
                "origin": f"{a.longitude:.6f},{a.latitude:.6f}",
                "destination": f"{b.longitude:.6f},{b.latitude:.6f}",
            }
            if mode in ("walking", "driving"):
                data = self._get(f"/v3/direction/{mode}", params)
                paths = data.get("route", {}).get("paths", [])
                if not paths:
                    raise ToolUnavailable("未查到该路段的导航路线")
                path = paths[0]
                distance = int(path["distance"])
                walking = distance if mode == "walking" else 0
            else:
                data = self._get("/v3/direction/transit/integrated", {**params, "city": self.city})
                paths = data.get("route", {}).get("transits", [])
                if not paths:
                    raise ToolUnavailable("未查到公共交通路线，请更换交通方式或候选地点")
                path = paths[0]
                distance = int(path.get("distance") or 0)
                walking = int(path["walking_distance"]) if path.get("walking_distance") else None
            leg = RouteLeg(
                origin_id=origin_id,
                destination_id=destination_id,
                duration_minutes=max(1, math.ceil(int(path["duration"]) / 60)),
                distance_meters=distance,
                walking_meters=walking,
                provider="amap",
                evidence="高德查询时的路线估计，未模拟未来日期路况；不包含完整景区内部步行。",
                mode=mode,
                route_status="queried",
            )
        self.cache[key] = leg
        self.trace.append(
            {
                "tool": "get_routes",
                "status": "ok",
                "provider": leg.provider,
                "origin": origin_id,
                "destination": destination_id,
            }
        )
        return leg

    def _auto_route(self, origin_id, a, destination_id, b) -> RouteLeg:
        key = (origin_id, destination_id, "auto")
        self.remaining_seconds()
        if key in self.cache:
            return self.cache[key]
        if self.settings.tool_provider == "amap" and not self.settings.amap_api_key:
            raise ToolUnavailable("高德 Key 尚未配置，请在网页 API 设置中填写自己的 Key")
        distance = distance_meters(a, b)
        hint = next(
            (
                item
                for item in self.guides.advice
                if destination_id in item.place_ids and (origin_id == "hotel" or origin_id in item.place_ids)
            ),
            None,
        )
        preferred = hint.mode if hint else "walking" if distance <= 1200 else "driving"
        if preferred == "walking" and distance > 4000:
            preferred = "driving"
        alternatives = [preferred]
        if preferred == "transit":
            alternatives.append("walking" if distance < 1000 else "driving")
        elif preferred == "walking" and distance > 1000:
            alternatives.append("driving")
        leg = None
        for mode in alternatives:
            try:
                leg = self.route(origin_id, a, destination_id, b, mode)
                break
            except ToolUnavailable as exc:
                if exc.fatal or self.route_calls >= self.settings.max_route_calls:
                    raise
                self.remaining_seconds()
        if leg is None:
            road_distance = round(distance * 1.35)
            duration = max(
                5,
                math.ceil(road_distance / (65 if preferred == "walking" else 250))
                + (0 if preferred == "walking" else 8),
            )
            leg = RouteLeg(
                origin_id=origin_id,
                destination_id=destination_id,
                duration_minutes=duration,
                distance_meters=road_distance,
                walking_meters=road_distance if preferred == "walking" else 0,
                provider="estimate",
                mode=preferred,
                evidence="导航查询未取得可用结果；按地点距离保守估时，未确认实际道路、班次与打车可用性。",
            )
            self.trace.append({"tool": "get_routes", "status": "estimated", "provider": "estimate"})
        label = {"walking": "步行", "driving": "打车 / 自驾", "transit": "公交 / 地铁"}[leg.mode]
        recommendation = (
            hint.advice
            if hint and hint.mode == leg.mode
            else (
                "相邻地点距离较近，建议步行，按体力和现场通行情况调整。"
                if leg.mode == "walking"
                else "本段距离较远，建议打车或自驾；实际叫车、费用和通行以出发时为准。"
                if leg.mode == "driving"
                else "可考虑公交或地铁，出发前核对站点、班次及末班时间。"
            )
        )
        leg = leg.model_copy(
            update={
                "recommendation": f"{label}：{recommendation}",
                "source_ids": hint.source_ids if hint and hint.mode == leg.mode else [],
            }
        )
        self.cache[key] = leg
        return leg

    def weather(self, start: date, days: int) -> ToolResult:
        observed = datetime.now(UTC).isoformat()
        if self.settings.tool_provider == "demo":
            result = ToolResult(
                status="partial",
                data={"days": [], "requested_start": str(start), "requested_days": days},
                provider="demo",
                observed_at=observed,
                evidence="演示模式不提供天气预测；请在出行前查询真实天气。",
            )
        else:
            try:
                adcode = self.adcode
                if not adcode:
                    geocodes = self._get("/v3/geocode/geo", {"address": self.city_query}).get("geocodes", [])
                    if not geocodes or not geocodes[0].get("adcode"):
                        raise ToolUnavailable("未取得当前城市的天气区域编码")
                    adcode = geocodes[0]["adcode"]
                self.adcode = adcode
                data = self._get("/v3/weather/weatherInfo", {"city": adcode, "extensions": "all"})
                requested = {(start.fromordinal(start.toordinal() + i)).isoformat() for i in range(days)}
                casts = {
                    cast["date"]: {**cast, "reporttime": forecast.get("reporttime", "")}
                    for forecast in data.get("forecasts", [])
                    for cast in forecast.get("casts", [])
                    if cast.get("date") in requested
                }
                matched = [casts[day] for day in sorted(casts)]
                result = ToolResult(
                    status="ok" if len(matched) == days else "partial",
                    data={
                        "days": matched,
                        "requested_start": str(start),
                        "requested_days": days,
                        "source_url": "https://lbs.amap.com/api/webservice/guide/api/weatherinfo",
                    },
                    provider="amap",
                    observed_at=observed,
                    evidence="高德逐日预报；不支持由逐日天气推断具体小时降雨，未覆盖日期保持未知。",
                )
            except ToolUnavailable as exc:
                result = ToolResult(
                    status="unavailable",
                    provider="amap",
                    observed_at=observed,
                    evidence="未取得天气数据",
                    error=str(exc),
                )
        self.trace.append({"tool": "get_weather", "status": result.status, "provider": result.provider})
        return result

    def _to_amap(self, location: Location | Place) -> Location | Place:
        if location.coordinate_system == "GCJ-02":
            return location
        key = (location.longitude, location.latitude)
        if key in self.converted_coordinates:
            lon, lat = self.converted_coordinates[key]
            return location.model_copy(
                update={"longitude": lon, "latitude": lat, "coordinate_system": "GCJ-02"}
            )
        result = self._get(
            "/v3/assistant/coordinate/convert",
            {
                "locations": f"{location.longitude},{location.latitude}",
                "coordsys": "gps",
            },
        )
        try:
            lon, lat = map(float, result["locations"].split(","))
        except (KeyError, ValueError, TypeError) as exc:
            raise ToolUnavailable("地图坐标转换未完成，不能混用不同坐标系") from exc
        self.converted_coordinates[key] = (lon, lat)
        return location.model_copy(update={"longitude": lon, "latitude": lat, "coordinate_system": "GCJ-02"})

    def search_candidates(
        self, query: str, category: str, anchor: Location | Place | None = None, *, limit: int = 5
    ) -> dict[str, Place]:
        """A bounded, city-restricted search through the shared per-key gateway."""
        if self.settings.tool_provider != "amap":
            raise ToolUnavailable("演示模式不联网补查新景点，请启用高德或使用已有目录地点。")
        params = {
            "keywords": query,
            "city": self.city,
            "citylimit": "true",
            "offset": min(6, max(1, limit)),
            "extensions": "base",
        }
        path = "/v3/place/text"
        if anchor:
            anchor = self._to_amap(anchor) if anchor.coordinate_system == "WGS84" else anchor
            path = "/v3/place/around"
            params.update(
                location=f"{anchor.longitude:.6f},{anchor.latitude:.6f}", radius=5000, sortrule="distance"
            )
        rows = self._get(path, params).get("pois", [])
        result = {}
        for row in rows[:limit]:
            try:
                if re.search(
                    r"(?:停车场|售票处|售票点|游客服务中心|公交站|地铁站|入口|出口|出入口)$"
                    r"|[-－].*(?:路|街|巷)\d+号$",
                    str(row.get("name", "")),
                ):
                    continue
                typecode = row.get("typecode")
                allowed_types = (
                    {"05"}
                    if category == "coffee"
                    else {"06", "11", "14"}
                    if category == "city"
                    else {"11", "14"}
                )
                if isinstance(typecode, str) and len(typecode) >= 2 and typecode[:2] not in allowed_types:
                    continue  # Stations, ticket counters and address labels are not attraction substitutes.
                returned_city = row.get("cityname")
                if (
                    isinstance(returned_city, str)
                    and returned_city
                    and returned_city.rstrip("市") != self.city.rstrip("市")
                ):
                    continue
                adcode = row.get("adcode")
                if self.adcode and isinstance(adcode, str):
                    width = 2 if self.adcode in {"110000", "120000", "310000", "500000"} else 4
                    if adcode[:width] != self.adcode[:width]:
                        continue
                place = provider_place(row, category, self.city)
                result[place.id] = place
            except (ValueError, KeyError, TypeError):
                continue
        self.trace.append(
            {
                "tool": "search_candidates",
                "status": "ok" if result else "no_result",
                "query": query,
                "count": len(result),
            }
        )
        return result

    def discover(self, destination: dict) -> dict:
        if self.settings.tool_provider != "amap":
            raise ToolUnavailable("请先在 API 设置中启用自己的高德 Key")
        data = self._get("/v3/geocode/geo", {"address": destination["state"] + destination["name"]})
        geocodes = data.get("geocodes", [])
        if not geocodes:
            raise ToolUnavailable("未识别到该城市，请核对城市名称或选择附近城市")
        geo = geocodes[0]
        self.adcode = geo.get("adcode")
        if not self.adcode:
            raise ToolUnavailable("地图服务未返回城市区域信息")
        self.city = destination["name"]
        places = {}
        for keyword, category, duration in [
            ("景点", "nature", 75),
            ("博物馆", "culture", 90),
            ("咖啡", "coffee", 45),
            ("步行街", "city", 60),
        ]:
            data = self._get(
                "/v3/place/text",
                {
                    "keywords": keyword,
                    "city": self.adcode,
                    "citylimit": "true",
                    "offset": 4,
                    "extensions": "all",
                },
            )
            for poi in data.get("pois", []):
                try:
                    lon, lat = map(float, poi["location"].split(","))
                    place_id = "amap-" + poi["id"]
                    places[place_id] = Place(
                        id=place_id,
                        name=poi["name"],
                        subtitle=poi.get("address") or destination["name"],
                        longitude=lon,
                        latitude=lat,
                        category=category,
                        indoor=category in ("culture", "coffee"),
                        duration_minutes=duration,
                        opening=None,
                        closing=None,
                        source_url="https://uri.amap.com/marker?position=" + poi["location"],
                        description="来自你本次高德查询的候选地点；建议停留时间与室内外属性为估算。",
                        evidence_note="高德 POI 查询快照。营业时间、预约、展览和建议停留时长尚未核验。",
                        fixture=False,
                    ).model_dump()
                except (ValueError, KeyError, TypeError):
                    continue
        if not places:
            raise ToolUnavailable("当前城市没有查到可用地点，请重试或切换演示模式")
        lon, lat = map(float, geo["location"].split(","))
        return {
            "places": places,
            "center": {
                "name": f"参考起点 · {destination['name']}",
                "longitude": lon,
                "latitude": lat,
                "coordinate_system": "GCJ-02",
            },
        }
