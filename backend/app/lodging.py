"""Shared lodging decisions and boundary identities; no model or transport calls here."""

from math import ceil

from app.schemas import HotelCandidate, HotelResearch, Location, Place, Requirements
from app.tools import distance_meters


def trip_points(req: Requirements, catalog: dict[str, Place]) -> dict[str, Place]:
    points = dict(catalog)
    for key in ("arrival", "departure"):
        boundary = getattr(req, key)
        if boundary:
            points[key] = Place(
                id=key,
                **boundary.location.model_dump(),
                subtitle="用户确认的抵离地点",
                category="city",
                indoor=False,
                duration_minutes=1,
                opening=None,
                closing=None,
                description="抵离接驳边界，不作为游玩景点。",
                source_url="",
                evidence_note="用户填写",
                fixture=False,
            )
    return points


def selected_hotel(lodging: HotelResearch, night: int) -> HotelCandidate | None:
    stay = next((stay for stay in lodging.nights if stay.night == night), None)
    return next((hotel for hotel in stay.candidates if hotel.id == stay.selected_id), None) if stay else None


def day_boundaries(req: Requirements, lodging: HotelResearch, day: int):
    if req.lodging_mode == "fixed":
        start, end = ("hotel", req.hotel), ("hotel", req.hotel)
    else:
        previous, following = selected_hotel(lodging, day - 1), selected_hotel(lodging, day)
        start = (f"hotel-night-{day - 1}", previous.location) if previous else (None, None)
        end = (f"hotel-night-{day}", following.location) if following else (None, None)
    if day == 1 and req.arrival:
        start = ("arrival", req.arrival.location)
    if day == req.days and req.departure:
        end = ("departure", req.departure.location)
    return start, end


def boundary_window(req: Requirements, day: int, offsets: dict) -> tuple[int, int]:
    def minute(value):
        return value.hour * 60 + value.minute

    window = req.day_windows.get(str(day))
    start = minute(window.start if window else req.day_start) + offsets.get(str(day), 0)
    end = minute(window.end if window else req.day_end)
    if day == 1 and req.arrival:
        start = max(start, minute(req.arrival.time) + req.arrival.buffer_minutes)
    if day == req.days and req.departure:
        end = min(end, minute(req.departure.time) - req.departure.buffer_minutes)
    return start, end


def estimate_minutes(distance: int) -> int:
    # Straight-line screening estimate, never represented as a navigation result.
    return ceil(distance * 1.3 / 75) if distance <= 1500 else ceil(distance * 1.4 / 350) + 5


def hotel_candidate(
    hotel_id: str,
    location: Location,
    last: Place,
    following: Place,
    *,
    provider: str,
    address: str = "",
    source_url: str = "",
    observed_at: str = "",
) -> HotelCandidate:
    evening, morning = distance_meters(last, location), distance_meters(location, following)
    return HotelCandidate(
        id=hotel_id,
        location=location,
        provider=provider,
        address=address[:300],
        source_url=source_url,
        observed_at=observed_at,
        evening_distance_meters=evening,
        morning_distance_meters=morning,
        evening_minutes=estimate_minutes(evening),
        morning_minutes=estimate_minutes(morning),
        reason="结合当晚末站与次日首站比较位置；通勤为直线距离推算，未查询实际导航。",
    )
