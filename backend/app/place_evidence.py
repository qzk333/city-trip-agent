"""Keep provider observations separate from heuristic travel attributes."""

from datetime import UTC, datetime

from app.schemas import AttributeEvidence, Location, Place, PlaceTraits
from app.travel_profile import THEMES


def effective_indoor(place: Place) -> bool:
    if place.traits.indoor_outdoor != "unknown":
        return place.traits.indoor_outdoor == "indoor"
    # Only explicit demo fixtures retain the old boolean as known test data.
    return place.fixture and place.indoor


def shelter_assessment(place, assessments=()):
    if place.traits.indoor_outdoor != "unknown":
        return place.traits.indoor_outdoor, "catalog"
    if place.fixture:
        return ("indoor" if place.indoor else "outdoor"), "fixture"
    assessment = next((a for a in assessments if a.place_id == place.id), None)
    if assessment and assessment.confidence != "low":
        return assessment.indoor_outdoor, "model"
    return "unknown", "unknown"


def provider_place(row: dict, category: str, city: str) -> Place:
    prefix = str(row.get("typecode", ""))[:2]
    category = {"14": "culture", "11": "nature", "05": "coffee", "06": "city"}.get(prefix, category)
    longitude, latitude = map(float, row["location"].split(","))
    location = Location(name=row["name"][:100], longitude=longitude, latitude=latitude)
    pid = "amap-" + str(row["id"])
    if not row["id"] or len(pid) > 100:
        raise ValueError("invalid POI identity")
    text = row["name"] + " " + str(row.get("type", ""))
    themes = [theme for theme, words in THEMES.items() if any(word in text for word in words)]
    duration = {"nature": 75, "culture": 90, "coffee": 45, "city": 60}[category]
    observed = datetime.now(UTC).isoformat()
    url = f"https://uri.amap.com/marker?position={longitude},{latitude}"
    return Place(
        id=pid,
        **location.model_dump(),
        category=category,
        indoor=False,
        duration_minutes=duration,
        opening=None,
        closing=None,
        subtitle=row.get("address") if isinstance(row.get("address"), str) else city,
        source_url=url,
        fixture=False,
        description="真实地图地点；旅行属性与建议停留时长仍需核实。",
        evidence_note=f"高德 POI 查询，{observed}；营业、预约、内部步行和适合人群尚未核验。",
        traits=PlaceTraits(
            activity_types=[str(row.get("typecode", ""))[:10]] if row.get("typecode") else [],
            themes=themes,
            duration_min=max(15, duration - 30),
            duration_max=duration + 30,
            unknowns=["室内外", "营业与预约", "景区内部步行/台阶", "适合人群"],
            evidence=[
                AttributeEvidence(
                    field="identity",
                    value=row["name"][:100],
                    kind="observed",
                    source_url=url,
                    observed_at=observed,
                    excerpt=str(row.get("type", ""))[:400],
                ),
                AttributeEvidence(
                    field="themes",
                    value=",".join(themes),
                    kind="inferred",
                    source_url=url,
                    observed_at=observed,
                    excerpt="按地点名称和地图分类匹配",
                ),
                AttributeEvidence(
                    field="duration",
                    value=f"{max(15, duration - 30)}～{duration + 30} 分钟",
                    kind="inferred",
                    observed_at=observed,
                    excerpt="按活动类型估算，未实地核实",
                ),
            ],
        ),
    )


def hard_opening_window(place):
    """Only observed opening evidence (or explicit demo fixtures) is a hard boundary."""
    if place.fixture:
        return place.opening, place.closing
    observed = {e.field for e in place.traits.evidence if e.kind == "observed" and e.source_url}
    return (
        place.opening if observed & {"opening", "opening_hours"} else None,
        place.closing if observed & {"closing", "opening_hours"} else None,
    )
