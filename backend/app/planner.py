from datetime import time, timedelta

from app.catalog import CATALOG
from app.schemas import Activity, Check, DayPlan, Itinerary, Place, Requirements
from app.tools import ToolUnavailable, TravelTools, distance_meters

TRANSFER_BUFFER = 10


def minutes(value: str | time) -> int:
    value = time.fromisoformat(value) if isinstance(value, str) else value
    return value.hour * 60 + value.minute


def clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def build_plan(
    req: Requirements,
    order: list[str],
    tools: TravelTools,
    overrides: dict,
    catalog: dict[str, Place] | None = None,
) -> Itinerary:
    catalog = catalog if catalog is not None else CATALOG
    checks = [
        Check(
            code="fixture_data",
            severity="warning",
            message=("地点坐标近似，营业时间与停留时间为演示夹具，尚未完成真实核验。"),
        ),
        Check(
            code="travel_estimates",
            severity="warning",
            message=("交通为估计值；景区内部步行、排队、预约及票务需另行确认。"),
        ),
    ]
    if not any(p.fixture for p in catalog.values()):
        checks[0] = Check(
            code="opening_hours_missing",
            severity="warning",
            message="地点来自本次地图查询；营业时间、预约与建议停留时间尚未核实。",
        )
    weather = tools.weather(req.start_date, req.days)
    if weather.status != "ok":
        checks.append(
            Check(code="weather_missing", severity="warning", message=weather.error or weather.evidence)
        )
    fixed = [
        Activity(
            id=f"d{a.day}-{a.place_id}-fixed{i}",
            place_id=a.place_id,
            day=a.day,
            start=a.start.strftime("%H:%M"),
            end=a.end.strftime("%H:%M"),
            locked=True,
            reason="用户确认的固定预约，保持时间不变",
        )
        for i, a in enumerate(req.appointments)
    ]
    fixed_by_id = {a.id: a for a in fixed}
    fixed_by_id.update({a["id"]: Activity(**a) for a in overrides.get("fixed", [])})
    fixed = list(fixed_by_id.values())
    fixed_places = {a.place_id for a in fixed}
    excluded = set(overrides.get("excluded", []))
    visited: set[str] = {a["place_id"] for d in overrides.get("frozen_days", []) for a in d["activities"]}
    preferred = overrides.get("preferred", order)
    candidate_ids = list(dict.fromkeys([*req.must_visit, *preferred, *order]))
    plans: list[DayPlan] = []
    direct_conflict = False
    for day_number in range(1, req.days + 1):
        frozen = next((d for d in overrides.get("frozen_days", []) if d["day"] == day_number), None)
        if frozen:
            plan = DayPlan(**frozen)
            plans.append(plan)
            visited.update(a.place_id for a in plan.activities)
            continue
        day_start = minutes(req.day_start) + overrides.get("delays", {}).get(str(day_number), 0)
        day_end = minutes(req.day_end)
        cursor = day_start
        origin_id, origin = "hotel", req.hotel
        activities: list[Activity] = []
        legs = []
        appointments = sorted((a for a in fixed if a.day == day_number), key=lambda a: a.start)
        for previous, following in zip(appointments, appointments[1:], strict=False):
            if minutes(previous.end) > minutes(following.start):
                direct_conflict = True
                checks.append(
                    Check(code="fixed_overlap", severity="error", message="固定活动时间重叠，请调整预约")
                )
        if any(minutes(a.start) < day_start or minutes(a.end) > day_end for a in appointments):
            direct_conflict = True
            checks.append(
                Check(code="fixed_outside_day", severity="error", message="固定活动超出当天可用时间")
            )
        cap = max(3 if req.pace == "relaxed" else 4, len(appointments))
        lunch_taken = False
        try:
            # Each fixed appointment forms a boundary; the final boundary is the hotel.
            for anchor in [*appointments, None]:
                limit = minutes(anchor.start) if anchor else day_end
                anchor_id = anchor.place_id if anchor else "hotel"
                anchor_place = catalog[anchor_id] if anchor else req.hotel
                while len(activities) < cap:
                    candidates = [
                        p
                        for p in candidate_ids
                        if p not in visited and p not in fixed_places and p not in excluded and p in catalog
                    ]
                    if overrides.get("indoor"):
                        candidates = [p for p in candidates if catalog[p].indoor or p in req.must_visit]
                    if "allowed_place_ids" in overrides:
                        candidates = [p for p in candidates if p in overrides["allowed_place_ids"]]
                    candidates.sort(
                        key=lambda p: (
                            p not in req.must_visit,
                            candidate_ids.index(p)
                            if overrides.get("preferred") or tools.settings.planning_mode == "llm"
                            else 0,
                            catalog[p].category not in req.interests,
                            distance_meters(origin, catalog[p]),
                        )
                    )
                    selected = None
                    for place_id in candidates:
                        place = catalog[place_id]
                        leg = tools.route(origin_id, origin, place_id, place, req.transport)
                        begin = max(
                            cursor + leg.duration_minutes + TRANSFER_BUFFER,
                            minutes(place.opening) if place.opening else day_start,
                        )
                        add_lunch = not lunch_taken and begin >= 12 * 60
                        if add_lunch:
                            begin += 45
                        finish = begin + place.duration_minutes
                        onward = tools.route(place_id, place, anchor_id, anchor_place, req.transport)
                        if (
                            finish <= min(day_end, minutes(place.closing) if place.closing else day_end)
                            and finish + onward.duration_minutes + TRANSFER_BUFFER <= limit
                        ):
                            selected = place, leg, begin, finish, add_lunch
                            break
                    if selected is None:
                        break
                    place, leg, begin, finish, add_lunch = selected
                    activities.append(
                        Activity(
                            id=f"d{day_number}-{place.id}",
                            place_id=place.id,
                            day=day_number,
                            start=clock(begin),
                            end=clock(finish),
                            reason=(
                                "你选择的必去地点" if place.id in req.must_visit else "结合兴趣与地点距离安排"
                            ),
                        )
                    )
                    legs.append(leg)
                    visited.add(place.id)
                    origin_id, origin, cursor = place.id, place, finish + (15 if req.pace == "relaxed" else 5)
                    lunch_taken |= add_lunch
                leg = tools.route(origin_id, origin, anchor_id, anchor_place, req.transport)
                if anchor:
                    legs.append(leg)
                    activities.append(anchor)
                    visited.add(anchor.place_id)
                    origin_id, origin, cursor = anchor.place_id, catalog[anchor.place_id], minutes(anchor.end)
                elif activities:
                    legs.append(leg)
        except ToolUnavailable as exc:
            checks.append(Check(code="route_unavailable", severity="error", message=str(exc)))
            tools.trace.append({"tool": "get_routes", "status": "unavailable", "message": str(exc)})
        plans.append(
            DayPlan(
                day=day_number,
                date=(req.start_date + timedelta(days=day_number - 1)).isoformat(),
                activities=activities,
                legs=legs,
            )
        )
    return Itinerary(
        status="CONFLICT" if direct_conflict else "NEEDS_CONFIRMATION",
        days=plans,
        checks=checks,
        weather=weather,
        planning_mode=tools.settings.planning_mode,
        tool_provider=tools.settings.tool_provider,
        trace=tools.trace,
        day_start_offsets=overrides.get("delays", {}),
        summary="已保留固定活动，并为交通和休息留出余量。所有演示信息仍需出行前核验。",
    )
