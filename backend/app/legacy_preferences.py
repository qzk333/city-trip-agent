"""One read boundary for old preference JSON; business logic only sees themes."""

LEGACY_THEMES = {"nature": "nature", "culture": "culture", "coffee": "coffee", "city": "city"}


def upgrade_preferences(value):
    if not isinstance(value, dict) or "interests" not in value:
        return value
    result = dict(value)
    old = result.pop("interests")
    if not isinstance(old, list) or any(
        not isinstance(item, str) or item not in LEGACY_THEMES for item in old
    ):
        raise ValueError("旧版兴趣字段无效，请重新选择主题")
    profile = result.get("profile") or {}
    if hasattr(profile, "model_dump"):
        profile = profile.model_dump()
    if not isinstance(profile, dict):
        return result  # Let the normal profile schema report its validation error.
    profile = dict(profile)
    # Explicit themes win over the old default nature/culture selection.
    if not profile.get("themes"):
        profile["themes"] = list(dict.fromkeys(LEGACY_THEMES[item] for item in old))
    result["profile"] = profile
    return result
