from datetime import timedelta

from app.candidate_ranking import excluded
from app.catalog import CATALOG
from app.lodging import boundary_transfer_needed, boundary_window, day_boundaries
from app.place_evidence import hard_opening_window
from app.planner import RECOMMENDED_TRANSFER_BUFFER, TRANSFER_BUFFER, clock, minutes
from app.review_agent import REVIEW_CODES, current_review, review_checks
from app.schedule_review import QUALITY_CODES
from app.schemas import Check, Itinerary, Place, Requirements
from app.travel_profile import normalize_profile

VALIDATION_CODES = {
    "duplicate_id",
    "duplicate_place",
    "day_coverage",
    "unknown_guide_source",
    "wrong_date",
    "empty_day",
    "route_coverage",
    "route_mismatch",
    "invalid_time",
    "late_arrival",
    "invalid_window",
    "unknown_place",
    "opening_hours",
    "return_mismatch",
    "late_return",
    "missing_required",
    "appointment_changed",
    "locked_changed",
    "lodging_pending",
    "lodging_invalid",
    "arrival_departure_unknown",
    "boundary_only_day",
    "place_day_changed",
    "excluded_place",
    "walking_unknown",
    "night_preference_unverified",
    "break_window",
    "break_overlap",
    "break_travel_conflict",
    *QUALITY_CODES,
    *REVIEW_CODES,
    "quality_review_unavailable",
    "quality_review_stale",
    "opening_hours_unverified",
    "transfer_buffer_low",
}


def validate_itinerary(
    req: Requirements,
    itinerary: Itinerary,
    overrides: dict | None = None,
    catalog: dict[str, Place] | None = None,
) -> Itinerary:
    """Validate a completed candidate independently of the scheduling search."""
    overrides = overrides or {}
    catalog = catalog if catalog is not None else CATALOG
    checks = [check for check in itinerary.checks if check.code not in VALIDATION_CODES]

    def error(code: str, message: str, activity_id: str | None = None):
        checks.append(Check(code=code, severity="error", message=message, activity_id=activity_id))

    all_activities = [a for day in itinerary.days for a in day.activities]
    profile = normalize_profile(req.profile, req.note)
    if "night" in profile.themes:
        checks.append(
            Check(
                code="night_preference_unverified",
                severity="warning",
                message="已记录夜景偏好，其匹配程度由整体审核判断；开放、照明与夜间通行仍需确认。",
            )
        )
    for activity in all_activities:
        place = catalog.get(activity.place_id)
        if place and excluded(place, profile):
            error("excluded_place", f"安排包含你明确排除的地点：{place.name}，请确认取舍。", activity.id)
    if profile.walking == "less" and any(
        not catalog[a.place_id].fixture for a in all_activities if a.place_id in catalog
    ):
        checks.append(
            Check(
                code="walking_unknown",
                severity="warning",
                message="已优先集中安排；景区内部步行和台阶资料尚未核实，不能保证少步行或无障碍。",
            )
        )
    if req.lodging_mode == "route_first":
        if not req.arrival or not req.departure:
            checks.append(
                Check(
                    code="arrival_departure_unknown",
                    severity="warning",
                    message="部分抵离信息未填写；未选住宿的路段将在选择酒店后补齐。",
                )
            )
        if req.days > 1 and (
            len(itinerary.lodging.nights) != req.days - 1
            or any(stay.selected_id is None for stay in itinerary.lodging.nights)
        ):
            checks.append(
                Check(
                    code="lodging_pending", severity="warning", message="当前为路线草案，住宿尚未全部选择。"
                )
            )
        if itinerary.lodging.nights and [stay.night for stay in itinerary.lodging.nights] != list(
            range(1, req.days)
        ):
            error("lodging_invalid", "住宿日期没有完整覆盖行程中的夜晚。")
        for stay in itinerary.lodging.nights:
            if (
                stay.checkin != (req.start_date + timedelta(days=stay.night - 1)).isoformat()
                or stay.checkout != (req.start_date + timedelta(days=stay.night)).isoformat()
                or (stay.selected_id and not any(h.id == stay.selected_id for h in stay.candidates))
            ):
                error("lodging_invalid", "住宿日期或酒店选择与已保存候选不一致。")
    seen_ids = set()
    seen_places = set()
    for activity in all_activities:
        if activity.id in seen_ids:
            error("duplicate_id", "活动 ID 重复", activity.id)
        seen_ids.add(activity.id)
        seen_places.add(activity.place_id)
    if [d.day for d in itinerary.days] != list(range(1, req.days + 1)):
        error("day_coverage", "行程没有完整覆盖旅行日期")
    for day in itinerary.days:
        earliest_window, latest_window = boundary_window(req, day.day, itinerary.day_start_offsets)
        breaks = sorted(day.breaks, key=lambda b: b.start)
        for index, block in enumerate(breaks):
            left, right = minutes(block.start), minutes(block.end)
            if left < earliest_window or right > latest_window:
                error("break_window", f"第 {day.day} 天用餐或休息超出可用时段。")
            if any(left < minutes(a.end) and right > minutes(a.start) for a in day.activities) or (
                index and left < minutes(breaks[index - 1].end)
            ):
                conflicting = next(
                    (a for a in day.activities if left < minutes(a.end) and right > minutes(a.start)), None
                )
                details = ""
                if conflicting:
                    place = catalog.get(conflicting.place_id)
                    overlap = min(right, minutes(conflicting.end)) - max(left, minutes(conflicting.start))
                    details = (
                        f"：{block.start}—{block.end} 与 {place.name if place else conflicting.place_id} "
                    )
                    details += f"{conflicting.start}—{conflicting.end} 重叠 {overlap} 分钟"
                error(
                    "break_overlap",
                    f"第 {day.day} 天用餐休息与活动或其他休息重叠{details}。",
                    conflicting.id if conflicting else None,
                )
        for leg in day.legs:
            if not set(leg.source_ids) <= {source.id for source in itinerary.guides.sources}:
                error("unknown_guide_source", "交通建议引用了未检索到的攻略来源")
        if day.date != (req.start_date + timedelta(days=day.day - 1)).isoformat():
            error("wrong_date", "行程日期与需求不一致")
        (start_id, start_place), (end_id, end_place) = day_boundaries(req, itinerary.lodging, day.day)
        if not day.activities:
            arrival_only = day.day == 1 and req.arrival is not None
            departure_only = day.day == req.days and req.departure is not None
            if not arrival_only and not departure_only:
                checks.append(
                    Check(
                        code="empty_day",
                        severity="warning",
                        message=f"第 {day.day} 天没有景点活动，休息或留白是否合理交由整体审核。",
                    )
                )
            else:
                checks.append(
                    Check(
                        code="boundary_only_day",
                        severity="warning",
                        message=f"第 {day.day} 天仅安排抵离接驳，不安排景点。",
                    )
                )
            expected = int(boundary_transfer_needed(req, day.day, start_place, end_place))
            if len(day.legs) != expected or (
                expected and (day.legs[0].origin_id != start_id or day.legs[0].destination_id != end_id)
            ):
                error("route_coverage", f"第 {day.day} 天的住宿或抵离接驳路线缺失或不匹配。")
            if expected and day.legs:
                earliest = (
                    minutes(req.arrival.time) + req.arrival.buffer_minutes
                    if arrival_only
                    else 0
                    if departure_only
                    else earliest_window
                )
                latest = (
                    minutes(req.departure.time) - req.departure.buffer_minutes
                    if departure_only
                    else 1440
                    if arrival_only
                    else latest_window
                )
                if earliest + day.legs[0].duration_minutes > latest:
                    error("late_return", "住宿或抵离接驳无法在当天的时间边界内完成。")
            continue
        if len(day.legs) != len(day.activities) - 1 + bool(start_place) + bool(end_place):
            error("route_coverage", f"第 {day.day} 天缺少与当前住宿选择相符的交通路段")
            continue
        offsets = overrides.get("delays", itinerary.day_start_offsets)
        earliest, latest = boundary_window(req, day.day, offsets)
        origin_id = start_id
        for index, activity in enumerate(day.activities):
            leg = day.legs[index if start_place else index - 1] if index or start_place else None
            if leg and (leg.origin_id != origin_id or leg.destination_id != activity.place_id):
                error("route_mismatch", "交通与活动衔接不匹配", activity.id)
            try:
                start, end = minutes(activity.start), minutes(activity.end)
            except ValueError:
                error("invalid_time", "活动时间格式错误", activity.id)
                continue
            if start < earliest + (leg.duration_minutes + TRANSFER_BUFFER if leg else 0):
                arrival = earliest + (leg.duration_minutes + TRANSFER_BUFFER if leg else 0)
                error(
                    "late_arrival",
                    f"第 {day.day} 天上一段交通后最早约 {clock(arrival)} 到达，"
                    f"无法赶上计划的 {activity.start} 活动；需调整前后时段。",
                    activity.id,
                )
            elif leg and start - earliest - leg.duration_minutes < RECOMMENDED_TRANSFER_BUFFER:
                checks.append(
                    Check(
                        code="transfer_buffer_low",
                        severity="warning",
                        activity_id=activity.id,
                        message=f"第 {day.day} 天此段交通后余量少于建议的10分钟；实际等候和进场时间需确认。",
                    )
                )
            reserved = sum(
                max(0, min(start, minutes(b.end)) - max(earliest, minutes(b.start))) for b in breaks
            )
            if reserved and start - earliest < reserved + (
                leg.duration_minutes + TRANSFER_BUFFER if leg else 0
            ):
                travel = leg.duration_minutes + TRANSFER_BUFFER if leg else 0
                error(
                    "break_travel_conflict",
                    f"第 {day.day} 天 {clock(earliest)}—{activity.start} 空档 "
                    f"{start - earliest} 分钟，已安排休息 {reserved} 分钟，还需交通及缓冲 {travel} 分钟，"
                    f"缺少 {reserved + travel - (start - earliest)} 分钟；请缩短或前移休息，或延后活动。",
                    activity.id,
                )
            if activity.day != day.day or end <= start or end > latest:
                error("invalid_window", "活动时间或所属日期超出允许范围", activity.id)
            place = catalog.get(activity.place_id)
            if place is None:
                error("unknown_place", "活动使用了目录以外的地点", activity.id)
            else:
                opening, closing = hard_opening_window(place)
                if (opening and start < minutes(opening)) or (closing and end > minutes(closing)):
                    error("opening_hours", "活动超出有来源依据的营业时间", activity.id)
                elif (place.opening and start < minutes(place.opening)) or (
                    place.closing and end > minutes(place.closing)
                ):
                    checks.append(
                        Check(
                            code="opening_hours_unverified",
                            severity="warning",
                            activity_id=activity.id,
                            message="活动与目录中未核实的营业时段不一致，需补充来源后确认；本项不阻止保存。",
                        )
                    )
            earliest, origin_id = end, activity.place_id
        if end_place:
            returning = day.legs[-1]
            if returning.origin_id != origin_id or returning.destination_id != end_id:
                error("return_mismatch", "回酒店路线缺失或不匹配")
            if earliest + returning.duration_minutes + TRANSFER_BUFFER > latest:
                error("late_return", "前往住处或返程站点的时间超出允许边界")
            reserved = sum(
                max(0, min(latest, minutes(b.end)) - max(earliest, minutes(b.start))) for b in breaks
            )
            if reserved and latest - earliest < reserved + returning.duration_minutes + TRANSFER_BUFFER:
                error("break_travel_conflict", "末站至住宿或返程站点的空档不足以同时完成交通及用餐休息。")
    visited = {a.place_id for a in all_activities}
    for place_id, assigned_day in req.place_days.items():
        if not any(a.place_id == place_id and a.day == assigned_day for a in all_activities):
            name = catalog[place_id].name if place_id in catalog else place_id
            error(
                "place_day_changed",
                f"用户指定的地点必须安排在第 {assigned_day} 天：{name}",
            )
    for place_id in req.must_visit:
        if place_id not in visited:
            error("missing_required", f"必去地点尚未排入：{catalog[place_id].name}")
    for index, appointment in enumerate(req.appointments):
        expected_id = f"d{appointment.day}-{appointment.place_id}-fixed{index}"
        actual = next((a for a in all_activities if a.id == expected_id), None)
        if (
            actual is None
            or actual.day != appointment.day
            or minutes(actual.start) != minutes(appointment.start)
            or minutes(actual.end) != minutes(appointment.end)
            or not actual.locked
        ):
            error("appointment_changed", "固定预约丢失或被移动", expected_id)
    for locked in overrides.get("fixed", []):
        actual = next((a for a in all_activities if a.id == locked["id"]), None)
        if actual is None or any(
            getattr(actual, k) != locked[k] for k in ("day", "place_id", "start", "end")
        ):
            error("locked_changed", "锁定活动丢失或被移动", locked["id"])
    if itinerary.planning_decision.source == "model_schedule":
        report = itinerary.planning_decision.quality_review
        if current_review(req, itinerary, catalog):
            checks.extend(review_checks(report))
        else:
            # A review of a different timetable cannot approve this one.
            if report.status in {"passed", "changes_requested"}:
                from app.schemas import ScheduleReview

                itinerary.planning_decision.quality_review = ScheduleReview()
                checks.append(
                    Check(
                        code="quality_review_stale",
                        severity="warning",
                        message="时间表已改变，旧体验审核已失效，需重新进行整体审核。",
                    )
                )
            if report.status == "unavailable":
                checks.append(
                    Check(code="quality_review_unavailable", severity="warning", message=report.summary)
                )
    has_errors = any(c.severity == "error" for c in checks)
    status = itinerary.status
    if status != "CONFLICT":
        status = (
            "NO_SOLUTION_WITHIN_LIMIT"
            if has_errors
            else "NEEDS_CONFIRMATION"
            if any(c.severity == "warning" for c in checks)
            else "FEASIBLE"
        )
    return itinerary.model_copy(update={"checks": checks, "status": status})
