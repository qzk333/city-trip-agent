import json

from app.config import ROOT
from app.geography import DEFAULT_CITY_ID, get_city
from app.schemas import Place


def load_catalog() -> dict[str, Place]:
    raw = json.loads((ROOT / "data/curated/hangzhou.demo.json").read_text(encoding="utf-8"))
    return {p["id"]: Place(**p, source_url="", evidence_note=raw["notice"]) for p in raw["places"]}


CATALOG = load_catalog()


def demo_catalog(city_id: int | None = None) -> dict[str, Place]:
    if city_id in (None, DEFAULT_CITY_ID):
        return dict(CATALOG)
    city = get_city(city_id)
    templates = [
        ("walk", "市中心漫步", "city", False, 60, 0.004, 0.003),
        ("culture", "文化活动备选", "culture", True, 75, -0.006, 0.004),
        ("park", "户外休息备选", "nature", False, 60, 0.008, -0.003),
        ("coffee", "咖啡休息备选", "coffee", True, 45, -0.003, -0.002),
        ("explore", "街区探索备选", "city", False, 60, 0.009, 0.007),
        ("exhibition", "展览活动备选", "culture", True, 75, -0.009, -0.005),
    ]
    result = {}
    for key, name, category, indoor, duration, dx, dy in templates:
        place_id = f"demo-{city['id']}-{key}"
        result[place_id] = Place(
            id=place_id,
            name=f"{city['name']} · {name}（示意）",
            subtitle="演示占位活动，非已核实地点",
            longitude=max(-180, min(180, city["longitude"] + dx)),
            latitude=max(-90, min(90, city["latitude"] + dy)),
            coordinate_system="WGS84",
            category=category,
            indoor=indoor,
            duration_minutes=duration,
            opening="09:00",
            closing="18:00",
            description="用于体验行程安排与调整；没有查询到真实商户或景点。",
            source_url="",
            evidence_note="自建演示占位活动，坐标在参考中心附近。请配置地图并加载真实地点。",
        )
    return result


def decode_catalog(raw: dict, city_id: int | None = None) -> dict[str, Place]:
    return {key: Place(**value) for key, value in raw.items()} if raw else demo_catalog(city_id)
