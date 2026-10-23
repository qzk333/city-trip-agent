import gzip
import json
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import ROOT

DEFAULT_CITY_ID = 19598
ALIASES = {
    "Hangzhou": "杭州",
    "Beijing": "北京",
    "Chongqing": "重庆",
    "Tianjin": "天津",
    "Chengdu": "成都",
    "Guangzhou": "广州",
    "Shenzhen": "深圳",
    "Nanjing": "南京",
    "Suzhou": "苏州",
    "Wuhan": "武汉",
    "Xi’an": "西安",
    "Xi'an": "西安",
    "Xian": "西安",
    "Ningbo": "宁波",
    "Wenzhou": "温州",
    "Jiaxing": "嘉兴",
    "Shaoxing": "绍兴",
    "Jinhua": "金华",
    "Huzhou": "湖州",
    "Quzhou": "衢州",
    "Zhoushan": "舟山",
    "Lishui": "丽水",
    "Taizhou": "台州",
    "Qingdao": "青岛",
    "Xiamen": "厦门",
    "Fuzhou": "福州",
    "Changsha": "长沙",
    "Zhengzhou": "郑州",
    "Jinan": "济南",
    "Hefei": "合肥",
    "Kunming": "昆明",
    "Dali": "大理",
    "Lijiang": "丽江",
    "Haikou": "海口",
    "Sanya": "三亚",
    "Guilin": "桂林",
    "Nanning": "南宁",
    "Tokyo": "东京",
    "Osaka": "大阪",
    "Kyoto": "京都",
    "Paris": "巴黎",
    "London": "伦敦",
    "New York City": "纽约",
    "Los Angeles": "洛杉矶",
    "Singapore": "新加坡",
}


@lru_cache(maxsize=1)
def regions() -> dict:
    return json.loads((ROOT / "data/geography/regions.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def city_index() -> dict[int, dict]:
    raw = json.loads(gzip.decompress((ROOT / "data/geography/cities.json.gz").read_bytes()))
    result = {}
    for row in raw:
        city_id, country, state, name, english, lon, lat, timezone = row
        foreign_aliases = {
            "Tokyo": "JP",
            "Osaka": "JP",
            "Kyoto": "JP",
            "Paris": "FR",
            "London": "GB",
            "New York City": "US",
            "Los Angeles": "US",
            "Singapore": "SG",
        }
        use_alias = country == "CN" or foreign_aliases.get(english) == country
        label = ALIASES.get(english, name) if use_alias else name
        if (state, english) == ("CN-AH", "Suzhou"):
            label = "宿州"
        if (state, english) == ("CN-JS", "Taizhou"):
            label = "泰州"
        result[city_id] = {
            "id": city_id,
            "country_code": country,
            "state_id": state,
            "name": label,
            "english_name": english,
            "longitude": lon,
            "latitude": lat,
            "timezone": timezone or None,
            "coordinate_system": "WGS84",
        }
    # The upstream snapshot has Shanghai districts but lacks the municipality itself.
    # This explicit local addition is a reference centre, not a verified hotel or POI.
    result[-310000] = {
        "id": -310000,
        "country_code": "CN",
        "state_id": "CN-SH",
        "name": "上海",
        "english_name": "Shanghai",
        "longitude": 121.4737,
        "latitude": 31.2304,
        "timezone": "Asia/Shanghai",
        "coordinate_system": "WGS84",
    }
    return result


@lru_cache(maxsize=1)
def grouped_cities() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for city in city_index().values():
        result.setdefault(city["state_id"], []).append(city)
    for values in result.values():
        values.sort(key=lambda c: (c["name"] == c["english_name"], c["name"]))
    return result


def get_city(city_id: int | None = None) -> dict:
    city = city_index().get(city_id if city_id is not None else DEFAULT_CITY_ID)
    if city is None:
        raise ValueError("城市不在目的地目录中，请重新选择")
    state = next(s for s in regions()["states"] if s["id"] == city["state_id"])
    country = next(c for c in regions()["countries"] if c["code"] == city["country_code"])
    return {**city, "state": state["name"], "country": country["name"]}


def validate_destination(req):
    city = get_city(req.city_id)
    if req.country_code != city["country_code"] or req.state_id != city["state_id"]:
        raise ValueError("国家、省份与城市不匹配，请重新选择")
    timezone = city["timezone"]
    if not timezone:
        raise ValueError("该城市缺少时区信息，暂不能生成有时间安排的行程")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("该城市时区无法识别，请选择其他城市") from exc
    return req.model_copy(
        update={
            "city_id": city["id"],
            "city": city["name"],
            "timezone": timezone,
            "province": city["state"],
            "country": city["country"],
        }
    )
