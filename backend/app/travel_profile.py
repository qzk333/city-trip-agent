"""Explicit preferences and conservative, inspectable keyword normalization."""

import hashlib
import json
import re

from app.schemas import TravelProfile

THEMES = {
    "history": ("历史", "文博", "博物馆", "古迹", "文化", "古城"),
    "architecture": ("建筑", "古建", "老街", "历史街区"),
    "night": ("夜景", "夜游", "夜间", "晚上拍"),
    "photo": ("摄影", "拍照", "出片"),
    "nature": ("自然", "公园", "湖泊", "风景"),
    "food": ("美食", "小吃", "当地菜"),
    "coffee": ("咖啡", "探店"),
    "culture": ("人文", "展览", "展馆", "艺术", "博物馆"),
    "city": ("街区", "街巷", "步行街", "城市漫步"),
}
THEME_NAMES = {
    "history": "历史文化",
    "architecture": "建筑街区",
    "night": "夜景",
    "photo": "摄影",
    "nature": "自然",
    "food": "美食",
    "coffee": "咖啡",
    "culture": "人文展览",
    "city": "城市漫步",
}


def normalize_profile(profile: TravelProfile, note: str) -> TravelProfile:
    result = profile.model_copy(deep=True)
    # Negative clauses are not positive interests; only these simple explicit exclusions are parsed.
    positive = re.sub(r"(?:不要|不去|不想去|不喜欢|不安排|少安排|避免)[^，。；,;\n]*", "", note)
    for theme, words in THEMES.items():
        if any(word in positive for word in words) and theme not in result.themes:
            result.themes.append(theme)
    if any(word in note for word in ("少走路", "少步行", "减少步行")):
        result.walking = "less"
    if result.companions == "unspecified":
        if any(word in note for word in ("父母", "爸妈")):
            result.companions = "parents"
        elif any(word in note for word in ("亲子", "带孩子")):
            result.companions = "children"
    for match in re.finditer(r"(?:不要|不去|不想去|不喜欢|不安排|避免)(购物|商场|咖啡|爬山|登山)", note):
        word = match.group(1)
        if word not in result.excluded_keywords:
            result.excluded_keywords.append(word)
    return result


def discovery_fingerprint(city_id, start_date, days, pace, profile, note) -> str:
    normalized = normalize_profile(profile, note)
    data = normalized.model_dump()
    data["themes"] = sorted(set(data["themes"]))
    data["excluded_keywords"] = sorted(set(data["excluded_keywords"]))
    payload = [2, city_id, str(start_date), days, pace, data, note]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
