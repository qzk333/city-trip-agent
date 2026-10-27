from datetime import time, timedelta
from math import ceil

from app.candidate_ranking import matches_themes
from app.catalog import CATALOG
from app.lodging import boundary_window, day_boundaries
from app.place_evidence import effective_indoor
from app.schemas import Activity, Check, DayPlan, HotelResearch, Itinerary, Place, Requirements, ToolResult
from app.timed_planning import build_timed_plan
from app.tools import ToolUnavailable, TravelTools, distance_meters
from app.travel_profile import normalize_profile

TRANSFER_BUFFER = 0  # Hard feasibility uses queried travel time, not a fixed extra quota.
RECOMMENDED_TRANSFER_BUFFER = 10


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
    profile = normalize_profile(req.profile, req.note)
    lodging = HotelResearch.model_validate(overrides.get("lodging", {}))
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
    weather = (
        ToolResult.model_validate(overrides["weather_snapshot"])
        if "weather_snapshot" in overrides
        else tools.weather(req.start_date, req.days)
    )
    if weather.status != "ok":
        checks.append(
            Check(code="weather_missing", severity="warning", message=weather.error or weather.evidence)
        )
    if overrides.get("model_schedule") is not None:
        return build_timed_plan(req, tools, overrides, catalog, weather, lodging, checks)
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
    candidate_ids = list(dict.fromkeys([*req.must_visit, *req.place_days, *preferred, *order]))
    if req.days > 1 and "allowed_place_ids" not in overrides:
        candidate_ids = list(dict.fromkeys([*candidate_ids, *catalog]))
    plans: list[DayPlan] = []
    direct_conflict = False
    for day_number in range(1, req.days + 1):
        frozen = next((d for d in overrides.get("frozen_days", []) if d["day"] == day_number), None)
        if frozen:
            plan = DayPlan(**frozen)
            plans.append(plan)
            visited.update(a.place_id for a in plan.activities)
            continue
        day_start, day_end = boundary_window(req, day_number, overrides.get("delays", {}))
        cursor = day_start
        (origin_id, origin), (end_id, end_place) = day_boundaries(req, lodging, day_number)
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
        available = [
            p
            for p in candidate_ids
            if p not in visited and p not in excluded and p not in fixed_places and p in catalog
        ]
        days_left = sum(
            not any(d["day"] == n for d in overrides.get("frozen_days", []))
            for n in range(day_number, req.days + 1)
        )
        target = max(1, ceil(len(available) / max(1, days_left)))
        cap = max(target, len(appointments))
        if day_start >= day_end and not appointments:
            cap = 0
        exact_ids = overrides.get("per_day_ids", {}).get(str(day_number))
        if exact_ids is not None:
            cap = len(exact_ids)
        groups = overrides.get("day_groups", [])
        group = groups[day_number - 1] if day_number <= len(groups) else []
        required_today = {pid for pid, assigned in req.place_days.items() if assigned == day_number}
        required_today.update(pid for pid in req.must_visit if req.days == 1 or pid in group)
        # Pace is a soft density preference; it must not silently cap mandatory activities.
        cap = max(cap, len(appointments) + len(required_today - fixed_places))
        lunch_taken = False
        try:
            # Each fixed appointment forms a boundary; the final boundary is the hotel.
            for anchor in [*appointments, None]:
                limit = minutes(anchor.start) if anchor else day_end
                anchor_id = anchor.place_id if anchor else end_id
                anchor_place = catalog[anchor_id] if anchor else end_place
                while len(activities) < cap:
                    candidates = [
                        p
                        for p in candidate_ids
                        if p not in visited
                        and p not in fixed_places
                        and p not in excluded
                        and p in catalog
                        and (p not in req.place_days or req.place_days[p] == day_number)
                    ]
                    if overrides.get("indoor"):
                        candidates = [
                            p for p in candidates if effective_indoor(catalog[p]) or p in req.must_visit
                        ]
                    if "allowed_place_ids" in overrides:
                        candidates = [p for p in candidates if p in overrides["allowed_place_ids"]]
                    if exact_ids is not None:
                        candidates = [p for p in candidates if p in exact_ids]
                        if anchor and anchor.place_id in exact_ids:
                            candidates = [
                                p for p in candidates if exact_ids.index(p) < exact_ids.index(anchor.place_id)
                            ]
                    candidates.sort(
                        key=lambda p: (
                            exact_ids.index(p) if exact_ids is not None else 0,
                            p not in req.must_visit,
                            p not in group if group else False,
                            candidate_ids.index(p) if overrides.get("preferred") else 0,
                            not matches_themes(catalog[p], profile),
                            distance_meters(origin, catalog[p]) if origin else 0,
                            candidate_ids.index(p),
                        )
                    )
                    selected = None
                    for place_id in candidates:
                        place = catalog[place_id]
                        try:
                            leg = (
                                tools.route(origin_id, origin, place_id, place, req.transport)
                                if origin
                                else None
                            )
                        except ToolUnavailable as exc:
                            if exc.fatal or tools.route_calls >= tools.settings.max_route_calls:
                                raise
                            continue
                        begin = max(
                            cursor + leg.duration_minutes + TRANSFER_BUFFER if leg else cursor,
                            minutes(place.opening) if place.opening else day_start,
                        )
                        add_lunch = not lunch_taken and begin >= 12 * 60
                        if add_lunch:
                            begin += 45
                        finish = begin + place.duration_minutes
                        try:
                            onward = (
                                tools.route(place_id, place, anchor_id, anchor_place, req.transport)
                                if anchor_place
                                else None
                            )
                        except ToolUnavailable as exc:
                            if exc.fatal or tools.route_calls >= tools.settings.max_route_calls:
                                raise
                            continue
                        if (
                            finish <= min(day_end, minutes(place.closing) if place.closing else day_end)
                            and finish + (onward.duration_minutes + TRANSFER_BUFFER if onward else 0) <= limit
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
                    if leg:
                        legs.append(leg)
                    visited.add(place.id)
                    origin_id, origin, cursor = place.id, place, finish + (15 if req.pace == "relaxed" else 5)
                    lunch_taken |= add_lunch
                leg = (
                    tools.route(origin_id, origin, anchor_id, anchor_place, req.transport)
                    if origin and anchor_place
                    else None
                )
                if anchor:
                    if leg:
                        legs.append(leg)
                    activities.append(anchor)
                    visited.add(anchor.place_id)
                    origin_id, origin, cursor = anchor.place_id, catalog[anchor.place_id], minutes(anchor.end)
                elif leg and (
                    activities
                    or (day_number == 1 and req.arrival)
                    or (day_number == req.days and req.departure)
                ):
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
    if req.use_web_guides and tools.guides.status in {"unavailable", "partial"}:
        checks.append(Check(code="guide_research_partial", severity="warning", message=tools.guides.note))
    if any(leg.provider == "estimate" for plan in plans for leg in plan.legs):
        checks.append(
            Check(
                code="route_estimated",
                severity="warning",
                message="部分路段未取得导航结果，使用距离估时并给出交通建议；出发前需确认实际路线与可用交通。",
            )
        )
    unique_checks = list({(check.code, check.message): check for check in checks}.values())
    return Itinerary(
        status="CONFLICT" if direct_conflict else "NEEDS_CONFIRMATION",
        days=plans,
        checks=unique_checks,
        weather=weather,
        planning_mode=tools.settings.planning_mode,
        tool_provider=tools.settings.tool_provider,
        trace=tools.trace,
        day_start_offsets=overrides.get("delays", {}),
        guides=tools.guides,
        lodging=lodging,
        summary=f"已按 {req.days} 天分配地点，为交通和休息留出余量；路线与攻略中的待确认信息请出行前核实。",
    )
