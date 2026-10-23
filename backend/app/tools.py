import math
from datetime import UTC, date, datetime
from time import monotonic

import httpx

from app.config import Settings
from app.schemas import Location, Place, RouteLeg, ToolResult


class ToolUnavailable(Exception):
    pass


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
            response = httpx.get(
                f"https://restapi.amap.com{path}",
                params={**params, "key": self.settings.amap_api_key},
                timeout=min(self.settings.tool_timeout_seconds, self.remaining_seconds()),
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "1":
                raise ToolUnavailable("地图服务拒绝请求，请检查接口权限、配额和配置")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolUnavailable("地图服务暂时不可用，请稍后重试") from exc

    def route(
        self, origin_id: str, a: Location | Place, destination_id: str, b: Location | Place, mode: str
    ) -> RouteLeg:
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
            )
        else:
            if a.coordinate_system == "WGS84" or b.coordinate_system == "WGS84":
                a, b = self._to_amap(a), self._to_amap(b)
            params = {"origin": f"{a.longitude},{a.latitude}", "destination": f"{b.longitude},{b.latitude}"}
            if mode == "walking":
                data = self._get("/v3/direction/walking", params)
                paths = data.get("route", {}).get("paths", [])
                if not paths:
                    raise ToolUnavailable("未查到步行路线，不能将缺失路线视为可达")
                path = paths[0]
                distance = int(path["distance"])
                walking = distance
            else:
                data = self._get(
                    "/v3/direction/transit/integrated", {**params, "city": self.adcode or self.city}
                )
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
                casts = [cast for forecast in data.get("forecasts", []) for cast in forecast.get("casts", [])]
                requested = {(start.fromordinal(start.toordinal() + i)).isoformat() for i in range(days)}
                matched = [cast for cast in casts if cast.get("date") in requested]
                result = ToolResult(
                    status="ok" if len(matched) == days else "partial",
                    data={"days": matched},
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
