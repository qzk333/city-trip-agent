"""Keep the model's explicit times; independently query and validate route feasibility."""

from datetime import timedelta

from app.lodging import boundary_transfer_needed, boundary_window, day_boundaries
from app.place_evidence import hard_opening_window
from app.schemas import Activity, Check, DayPlan, Itinerary, ScheduledDay
from app.tools import ToolUnavailable


def incoming_legs(activities, legs, end_place):
    # Match each visit by sequence: the same POI may be visited more than once.
    offset = 0 if len(legs) - bool(end_place) == len(activities) else -1
    return [
        legs[i + offset] if i + offset >= 0 and i + offset < len(legs) else None
        for i in range(len(activities))
    ]


def align_short_delays(req, proposed, activities, legs, options, catalog, end_place=None):
    """Query-derived corrections are explicit and bounded; fixed user times never move."""
    from app.planner import TRANSFER_BUFFER, clock, minutes

    cursor, latest = boundary_window(req, proposed.day, options.get("delays", {}))
    notes = []
    originals = [(minutes(a.start), minutes(a.end)) for a in activities]
    routes = incoming_legs(activities, legs, end_place)
    for index, a in enumerate(activities):
        leg = routes[index]
        earliest = cursor + (leg.duration_minutes + TRANSFER_BUFFER if leg else 0)
        p = catalog[a.place_id]
        opening, closing_time = hard_opening_window(p)
        earliest = max(earliest, minutes(opening) if opening else earliest)
        start, end = minutes(a.start), minutes(a.end)
        delay = earliest - start
        closing = min(latest, minutes(closing_time) if closing_time else latest)
        if not a.locked and 0 < delay and delay <= 30 and end + delay <= closing:
            a.start, a.end = clock(start + delay), clock(end + delay)
            notes.append(
                Check(
                    code="activity_time_adjusted",
                    severity="info",
                    message=f"第 {proposed.day} 天结合交通及可用窗口，"
                    f"将 {p.name} "
                    f"从 {clock(start)}—{clock(end)} 微调为 {a.start}—{a.end}（顺延 {delay} 分钟）；"
                    "停留时长保留，固定预约不移动。",
                )
            )
        cursor = minutes(a.end)
    start, _ = boundary_window(req, proposed.day, options.get("delays", {}))
    old_gaps = list(
        zip(
            [start, *[end for _, end in originals]], [*[start for start, _ in originals], latest], strict=True
        )
    )
    new_gaps = list(
        zip(
            [start, *[minutes(a.end) for a in activities]],
            [*[minutes(a.start) for a in activities], latest],
            strict=True,
        )
    )
    for block in proposed.breaks:
        left, right = minutes(block.start), minutes(block.end)
        for (old_left, old_right), (new_left, new_right) in zip(old_gaps, new_gaps, strict=True):
            if old_left <= left < right <= old_right:
                clipped_left, clipped_right = max(left, new_left), min(right, new_right)
                minimum = 30 if block.kind == "meal" else 15
                if clipped_right - clipped_left >= minimum and (clipped_left, clipped_right) != (left, right):
                    block.start, block.end = clock(clipped_left), clock(clipped_right)
                    notes.append(
                        Check(
                            code="break_adjusted",
                            severity="info",
                            message=f"第 {proposed.day} 天配合交通顺延，将软休息 "
                            f"{clock(left)}—{clock(right)} "
                            f"调整为 {block.start}—{block.end}。",
                        )
                    )
                break
    return notes


def repair_time_chain(req, proposed, activities, legs, options, catalog, end_place):
    """Jointly fit a chain after cascading delays, preserving order and user anchors."""
    from app.planner import TRANSFER_BUFFER, clock, minutes

    start, end = boundary_window(req, proposed.day, options.get("delays", {}))
    incoming = [
        leg.duration_minutes + TRANSFER_BUFFER if leg else 0
        for leg in incoming_legs(activities, legs, end_place)
    ]
    returning = legs[-1].duration_minutes + TRANSFER_BUFFER if end_place and legs else 0
    cursor = start
    broken = False
    for i, a in enumerate(activities):
        broken |= minutes(a.start) < cursor + incoming[i] or minutes(a.end) > end
        cursor = minutes(a.end)
    broken |= cursor + returning > end
    if not broken:
        return []
    durations = [minutes(a.end) - minutes(a.start) for a in activities]
    lower, upper = [], []
    for a, duration in zip(activities, durations, strict=True):
        p = catalog[a.place_id]
        opening, closing = hard_opening_window(p)
        left = max(start, minutes(opening) if opening else start)
        right = min(end, minutes(closing) if closing else end) - duration
        if a.locked:
            left = max(left, minutes(a.start))
            right = min(right, minutes(a.start))
        lower.append(left)
        upper.append(right)
    lower[0] = max(lower[0], start + incoming[0])
    upper[-1] = min(upper[-1], end - returning - durations[-1])
    for i in range(1, len(activities)):
        lower[i] = max(lower[i], lower[i - 1] + durations[i - 1] + incoming[i])
    for i in range(len(activities) - 2, -1, -1):
        upper[i] = min(upper[i], upper[i + 1] - incoming[i + 1] - durations[i])
    if any(left > right for left, right in zip(lower, upper, strict=True)):
        return [
            Check(
                code="time_chain_repair_incomplete",
                severity="warning",
                message=f"第 {proposed.day} 天在当前地点顺序、停留时长和固定窗口下未能联合修复；"
                "已保留具体冲突供主模型取舍，未移动预约或删除活动。",
            )
        ]
    selected = []
    for i, _a in enumerate(activities):
        earliest = max(lower[i], selected[-1] + durations[i - 1] + incoming[i] if i else start + incoming[0])
        selected.append(max(earliest, min(minutes(proposed.activities[i].start), upper[i])))
    notes = []
    for a, new_start, duration in zip(activities, selected, durations, strict=True):
        if minutes(a.start) == new_start:
            continue
        old = f"{a.start}—{a.end}"
        a.start, a.end = clock(new_start), clock(new_start + duration)
        notes.append(
            Check(
                code="activity_time_adjusted",
                severity="info",
                message=f"第 {proposed.day} 天为修复连锁衔接，将 {catalog[a.place_id].name} "
                f"从 {old} 联合调整为 {a.start}—{a.end}；顺序和时长保留，固定预约不移动。",
            )
        )
    return notes


def repair_overlapping_breaks(req, proposed, activities, legs, options, end_place):
    """Reinsert soft meals/rest in genuine free windows; never discard a meal to pass."""
    from app.planner import TRANSFER_BUFFER, clock, minutes

    ordered = sorted(proposed.breaks, key=lambda b: b.start)
    conflict = any(
        any(minutes(b.start) < minutes(a.end) and minutes(b.end) > minutes(a.start) for a in activities)
        or (i and minutes(b.start) < minutes(ordered[i - 1].end))
        for i, b in enumerate(ordered)
    )
    window_start, window_end = boundary_window(req, proposed.day, options.get("delays", {}))
    previous = window_start
    for activity, incoming in zip(activities, incoming_legs(activities, legs, end_place), strict=True):
        reserved = sum(
            max(0, min(minutes(activity.start), minutes(b.end)) - max(previous, minutes(b.start)))
            for b in ordered
        )
        if (
            incoming
            and reserved + incoming.duration_minutes + TRANSFER_BUFFER > minutes(activity.start) - previous
        ):
            conflict = True
        previous = minutes(activity.end)
    if end_place and legs:
        reserved = sum(
            max(0, min(window_end, minutes(b.end)) - max(previous, minutes(b.start))) for b in ordered
        )
        if reserved + legs[-1].duration_minutes + TRANSFER_BUFFER > window_end - previous:
            conflict = True
    if not conflict:
        return []
    start, end = boundary_window(req, proposed.day, options.get("delays", {}))
    occupied = []
    for a, leg in zip(activities, incoming_legs(activities, legs, end_place), strict=True):
        occupied.append(
            (minutes(a.start) - (leg.duration_minutes + TRANSFER_BUFFER if leg else 0), minutes(a.end))
        )
    returning = legs[-1].duration_minutes + TRANSFER_BUFFER if end_place and legs else 0
    windows, cursor = [], start
    for left, right in sorted(occupied):
        if left > cursor:
            windows.append((cursor, min(left, end - returning)))
        cursor = max(cursor, right)
    if cursor < end - returning:
        windows.append((cursor, end - returning))
    windows = [(left, right) for left, right in windows if right > left]
    placed, notes = [], []
    for b in sorted(ordered, key=lambda b: (b.kind != "meal", b.start)):
        left, right = minutes(b.start), minutes(b.end)
        duration = right - left
        minimum = min(duration, 30 if b.kind == "meal" else 15)
        choices = []
        for i, (wstart, wend) in enumerate(windows):
            length = min(duration, wend - wstart)
            if length < minimum:
                continue
            proposed_start = max(wstart, min(left, wend - length))
            if abs(proposed_start - left) > 120:
                continue
            score = abs(proposed_start - left) + 2 * (duration - length)
            choices.append((score, i, proposed_start, proposed_start + length))
        if not choices:
            if b.kind == "meal":
                placed.append(b)  # Independent validation keeps the unresolved conflict visible.
            else:
                notes.append(
                    Check(
                        code="break_adjusted",
                        severity="info",
                        message=f"第 {proposed.day} 天将重复或冲突的非必要休息 {b.start}—{b.end} "
                        "合并到其他可用休息安排。",
                    )
                )
            continue
        _, index, new_start, new_end = min(choices)
        wstart, wend = windows.pop(index)
        if wstart < new_start:
            windows.append((wstart, new_start))
        if new_end < wend:
            windows.append((new_end, wend))
        placed.append(b.model_copy(update={"start": clock(new_start), "end": clock(new_end)}))
        if (new_start, new_end) != (left, right):
            notes.append(
                Check(
                    code="break_adjusted",
                    severity="info",
                    message=f"第 {proposed.day} 天为消除重叠，将{'用餐' if b.kind == 'meal' else '休息'} "
                    f"{b.start}—{b.end} 调整为 {clock(new_start)}—{clock(new_end)}，并为实际转场另留时间。",
                )
            )
    proposed.breaks = sorted(placed, key=lambda b: b.start)
    return notes


def reserve_travel_in_breaks(req, proposed, activities, legs, options, end_place):
    """Only model-proposed soft breaks may shrink; activity/appointment times stay exact."""
    from app.planner import TRANSFER_BUFFER, clock, minutes

    start, end = boundary_window(req, proposed.day, options.get("delays", {}))
    gaps = []
    previous = start
    for a, leg in zip(activities, incoming_legs(activities, legs, end_place), strict=True):
        gaps.append((previous, minutes(a.start), leg))
        previous = minutes(a.end)
    if end_place and legs:
        gaps.append((previous, end, legs[-1]))
    notes = []
    for left, right, leg in gaps:
        if leg is None:
            continue
        blocks = sorted(
            [b for b in proposed.breaks if minutes(b.start) >= left and minutes(b.end) <= right],
            key=lambda b: b.start,
            reverse=True,
        )
        occupied = sum(minutes(b.end) - minutes(b.start) for b in blocks)
        excess = occupied + leg.duration_minutes + TRANSFER_BUFFER - (right - left)
        for block in blocks:
            if excess <= 0:
                break
            duration = minutes(block.end) - minutes(block.start)
            minimum = 30 if block.kind == "meal" else 15
            reduction = min(excess, max(0, duration - minimum))
            if not reduction:
                continue
            original_end = block.end
            block.end = clock(minutes(block.end) - reduction)
            excess -= reduction
            notes.append(
                Check(
                    code="break_adjusted",
                    severity="info",
                    message=f"第 {proposed.day} 天按查询交通及缓冲预留时间，"
                    f"将可调整的{'用餐' if block.kind == 'meal' else '休息/自由安排'} "
                    f"{block.start}—{original_end} 缩短为 {block.start}—{block.end}；活动时段保持不变。",
                )
            )
    return notes


def build_timed_plan(req, tools, options, catalog, weather, lodging, checks):
    fixed = [(f"d{a.day}-{a.place_id}-fixed{i}", a) for i, a in enumerate(req.appointments)]
    saved_locks = list(options.get("fixed", []))
    preserved = {
        (a["day"], a["place_id"], a["start"], a["end"]): a["id"]
        for a in options.get("preserved_activity_ids", [])
    }
    proposed_by_day = {raw["day"]: raw for raw in options["model_schedule"]}
    frozen = {raw["day"]: raw for raw in options.get("frozen_days", [])}
    plans = []
    for day_number in range(1, req.days + 1):
        if day_number in frozen:
            plans.append(DayPlan.model_validate(frozen[day_number]))
            continue
        raw = proposed_by_day[day_number]
        proposed = ScheduledDay.model_validate(raw)
        activities = []
        used_ids = set()
        visits = {}
        for stop in proposed.activities:
            candidates = [
                (aid, a)
                for aid, a in fixed
                if a.day == proposed.day and a.place_id == stop.place_id and aid not in used_ids
            ]
            appointment = next(
                (
                    (aid, a)
                    for aid, a in candidates
                    if a.start.strftime("%H:%M") == stop.start and a.end.strftime("%H:%M") == stop.end
                ),
                candidates[0] if candidates else None,
            )
            locks = [
                a
                for a in saved_locks
                if a["day"] == proposed.day and a["place_id"] == stop.place_id and a["id"] not in used_ids
            ]
            lock = next(
                (a for a in locks if a["start"] == stop.start and a["end"] == stop.end),
                locks[0] if locks else None,
            )
            visits[stop.place_id] = visits.get(stop.place_id, 0) + 1
            base_id = f"d{proposed.day}-{stop.place_id}"
            activity_id = (
                appointment[0]
                if appointment
                else lock["id"]
                if lock
                else preserved.get((proposed.day, stop.place_id, stop.start, stop.end))
                or (base_id if visits[stop.place_id] == 1 else f"{base_id}-visit{visits[stop.place_id]}")
            )
            used_ids.add(activity_id)
            activities.append(
                Activity(
                    id=activity_id,
                    place_id=stop.place_id,
                    day=proposed.day,
                    start=stop.start,
                    end=stop.end,
                    locked=appointment is not None or lock is not None,
                    reason=stop.reason or "模型结合时间、天气与旅行偏好安排",
                    time_intent=stop.time_intent,
                )
            )
        (origin_id, origin), (end_id, end_place) = day_boundaries(req, lodging, proposed.day)
        has_start = origin is not None
        legs = []
        try:
            for activity in activities:
                place = catalog[activity.place_id]
                if origin:
                    legs.append(tools.route(origin_id, origin, activity.place_id, place, req.transport))
                origin_id, origin = activity.place_id, place
            if (
                origin
                and end_place
                and (activities or boundary_transfer_needed(req, proposed.day, origin, end_place))
            ):
                legs.append(tools.route(origin_id, origin, end_id, end_place, req.transport))
        except ToolUnavailable as exc:
            checks.append(Check(code="route_unavailable", severity="error", message=str(exc)))
        if activities and len(legs) == len(activities) - 1 + has_start + bool(end_place):
            checks.extend(align_short_delays(req, proposed, activities, legs, options, catalog, end_place))
            checks.extend(repair_time_chain(req, proposed, activities, legs, options, catalog, end_place))
            checks.extend(reserve_travel_in_breaks(req, proposed, activities, legs, options, end_place))
            checks.extend(repair_overlapping_breaks(req, proposed, activities, legs, options, end_place))
        plans.append(
            DayPlan(
                day=proposed.day,
                date=(req.start_date + timedelta(days=proposed.day - 1)).isoformat(),
                activities=activities,
                legs=legs,
                breaks=proposed.breaks,
                weather_note=proposed.weather_note,
            )
        )
    if req.use_web_guides and tools.guides.status in {"unavailable", "partial"}:
        checks.append(Check(code="guide_research_partial", severity="warning", message=tools.guides.note))
    return Itinerary(
        status="NEEDS_CONFIRMATION",
        days=plans,
        checks=checks,
        weather=weather,
        trace=tools.trace,
        planning_mode=tools.settings.planning_mode,
        tool_provider=tools.settings.tool_provider,
        guides=tools.guides.model_copy(deep=True),
        lodging=lodging,
        day_start_offsets=options.get("delays", {}),
        summary=f"模型已提出 {req.days} 天的活动时段与用餐休息，交通及硬条件由程序独立复核；"
        "营业与预约仍需确认。",
    )
