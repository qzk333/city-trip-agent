"""Apply an explicit user lodging decision without asking agents to regenerate attractions."""

from app.database import now
from app.lodging import boundary_transfer_needed, day_boundaries, hotel_candidate, trip_points
from app.schemas import HotelChoiceRequest, Itinerary, Place, Requirements, ScheduledDay
from app.timed_planning import (
    align_short_delays,
    repair_overlapping_breaks,
    repair_time_chain,
    reserve_travel_in_breaks,
)
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
    fixed = [a.model_dump() for d in base.days for a in d.activities if a.locked]
    options = {"delays": result.day_start_offsets, "fixed": fixed}
    for day in result.days:
        if day.day not in {choice.night, choice.night + 1}:
            continue
        (start_id, start), (end_id, end) = day_boundaries(req, result.lodging, day.day)
        if not day.activities:
            day.legs = (
                [tools.route(start_id, start, end_id, end, req.transport)]
                if boundary_transfer_needed(req, day.day, start, end)
                else []
            )
            continue
        # Slice using the OLD boundary: the new hotel inserts a leg and repeated
        # visits can share endpoint IDs but have different saved route evidence.
        (_, old_start), (_, old_end) = day_boundaries(req, base.lodging, day.day)
        offset = int(old_start is not None)
        if len(day.legs) != len(day.activities) - 1 + offset + bool(old_end):
            raise ToolUnavailable("原路线缺少景点间交通，请重新规划后选择酒店。")
        internal = day.legs[offset : offset + len(day.activities) - 1]
        for previous, following, leg in zip(day.activities, day.activities[1:], internal, strict=False):
            if leg.origin_id != previous.place_id or leg.destination_id != following.place_id:
                raise ToolUnavailable("原路线缺少景点间交通，请重新规划后选择酒店。")
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
        proposed = ScheduledDay(
            day=day.day,
            activities=[
                a.model_dump(include={"place_id", "start", "end", "reason", "time_intent"})
                for a in day.activities
            ],
            breaks=[b.model_copy(deep=True) for b in day.breaks],
            weather_note=day.weather_note,
        )
        result.checks.extend(
            align_short_delays(req, proposed, day.activities, day.legs, options, catalog, end)
        )
        result.checks.extend(
            repair_time_chain(req, proposed, day.activities, day.legs, options, catalog, end)
        )
        result.checks.extend(reserve_travel_in_breaks(req, proposed, day.activities, day.legs, options, end))
        result.checks.extend(repair_overlapping_breaks(req, proposed, day.activities, day.legs, options, end))
        day.breaks = proposed.breaks
    result.trace = [
        *result.trace,
        {"tool": "select_hotel", "status": "user_selected", "night": choice.night},
        *tools.trace,
    ]
    result.summary = f"已按第 {choice.night} 晚住宿选择补齐相邻两天交通；景点顺序和锁定活动保持原样。"
    # If the new boundary makes a locked appointment impossible, validation blocks confirmation.
    if result.status != "CONFLICT":
        result.status = "NEEDS_CONFIRMATION"
    return validate_itinerary(req, result, options, catalog)
