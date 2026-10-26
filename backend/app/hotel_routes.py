"""Apply an explicit user lodging decision without asking agents to regenerate attractions."""

from app.database import now
from app.lodging import boundary_window, day_boundaries, hotel_candidate, trip_points
from app.planner import TRANSFER_BUFFER, clock, minutes
from app.schemas import HotelChoiceRequest, Itinerary, Place, Requirements
from app.tools import ToolUnavailable, TravelTools
from app.validation import validate_itinerary


def select_hotel(
    req: Requirements,
    base: Itinerary,
    choice: HotelChoiceRequest,
    tools: TravelTools,
    catalog: dict[str, Place],
) -> Itinerary:
    if req.lodging_mode != "route_first":
        raise ToolUnavailable("当前行程使用固定住处，请创建路线优先的行程。")
    result = base.model_copy(deep=True)
    catalog = trip_points(req, catalog)
    stay = next((n for n in result.lodging.nights if n.night == choice.night), None)
    if stay is None or not stay.last_place_id or not stay.next_place_id:
        raise ToolUnavailable("这一晚尚无可用于住宿选择的路线，请先补齐相邻两天行程。")
    if choice.custom_location:
        candidate = hotel_candidate(
            f"user-hotel-night-{stay.night}",
            choice.custom_location,
            catalog[stay.last_place_id],
            catalog[stay.next_place_id],
            provider="user",
            observed_at=now(),
        )
        stay.candidates = [h for h in stay.candidates if h.id != candidate.id][:7] + [candidate]
        stay.selected_id = candidate.id
    else:
        if not any(h.id == choice.candidate_id for h in stay.candidates):
            raise ToolUnavailable("酒店不在该晚保存的候选中，请刷新后选择。")
        stay.selected_id = choice.candidate_id
    tools.guides = result.guides
    for day in result.days:
        if day.day not in {choice.night, choice.night + 1}:
            continue
        (start_id, start), (end_id, end) = day_boundaries(req, result.lodging, day.day)
        if not day.activities:
            day.legs = [tools.route(start_id, start, end_id, end, req.transport)] if start and end else []
            continue
        # Internal attraction legs are immutable for this operation. Match by identity,
        # rather than list offsets, which change when lodging legs are inserted.
        internal = []
        for previous, following in zip(day.activities, day.activities[1:], strict=False):
            leg = next(
                (
                    leg
                    for leg in day.legs
                    if leg.origin_id == previous.place_id and leg.destination_id == following.place_id
                ),
                None,
            )
            if leg is None:
                raise ToolUnavailable("原路线缺少景点间交通，请重新规划后选择酒店。")
            internal.append(leg)
        saved_incoming = next(
            (
                leg
                for leg in day.legs
                if leg.origin_id == start_id and leg.destination_id == day.activities[0].place_id
            ),
            None,
        )
        saved_outgoing = next(
            (
                leg
                for leg in day.legs
                if leg.origin_id == day.activities[-1].place_id and leg.destination_id == end_id
            ),
            None,
        )
        incoming = (
            saved_incoming
            if day.day != choice.night + 1 and saved_incoming
            else (
                tools.route(
                    start_id,
                    start,
                    day.activities[0].place_id,
                    catalog[day.activities[0].place_id],
                    req.transport,
                )
                if start
                else None
            )
        )
        outgoing = (
            saved_outgoing
            if day.day != choice.night and saved_outgoing
            else (
                tools.route(
                    day.activities[-1].place_id,
                    catalog[day.activities[-1].place_id],
                    end_id,
                    end,
                    req.transport,
                )
                if end
                else None
            )
        )
        day.legs = ([incoming] if incoming else []) + internal + ([outgoing] if outgoing else [])
        earliest, latest = boundary_window(req, day.day, result.day_start_offsets)
        for index, activity in enumerate(day.activities):
            leg = incoming if index == 0 else internal[index - 1]
            arrival = earliest + (leg.duration_minutes + TRANSFER_BUFFER if leg else 0)
            if minutes(activity.start) < arrival and not activity.locked:
                duration = minutes(activity.end) - minutes(activity.start)
                if arrival + duration <= latest:
                    activity.start, activity.end = clock(arrival), clock(arrival + duration)
            earliest = minutes(activity.end)
    result.trace = [
        *result.trace,
        {"tool": "select_hotel", "status": "user_selected", "night": choice.night},
        *tools.trace,
    ]
    result.summary = f"已按第 {choice.night} 晚住宿选择补齐相邻两天交通；景点顺序和锁定活动保持原样。"
    # If the new boundary makes a locked appointment impossible, validation blocks confirmation.
    if result.status != "CONFLICT":
        result.status = "NEEDS_CONFIRMATION"
    return validate_itinerary(req, result, catalog=catalog)
