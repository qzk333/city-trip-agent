from datetime import timedelta

from app.catalog import CATALOG
from app.planner import TRANSFER_BUFFER, minutes
from app.schemas import Check, Itinerary, Requirements


def validate_itinerary(req: Requirements, itinerary: Itinerary, overrides: dict | None = None) -> Itinerary:
    """Validate a completed candidate independently of the scheduling search."""
    overrides = overrides or {}
    checks = list(itinerary.checks)

    def error(code: str, message: str, activity_id: str | None = None):
        checks.append(Check(code=code, severity="error", message=message, activity_id=activity_id))

    all_activities = [a for day in itinerary.days for a in day.activities]
    seen_ids = set()
    for activity in all_activities:
        if activity.id in seen_ids:
            error("duplicate_id", "活动 ID 重复", activity.id)
        seen_ids.add(activity.id)
    if [d.day for d in itinerary.days] != list(range(1, req.days + 1)):
        error("day_coverage", "行程没有完整覆盖旅行日期")
    for day in itinerary.days:
        if day.date != (req.start_date + timedelta(days=day.day - 1)).isoformat():
            error("wrong_date", "行程日期与需求不一致")
        if not day.activities:
            error("empty_day", f"第 {day.day} 天未找到可用安排")
            continue
        if len(day.legs) != len(day.activities) + 1:
            error("route_coverage", f"第 {day.day} 天缺少完整往返交通，不能确认可执行")
            continue
        offsets = overrides.get("delays", itinerary.day_start_offsets)
        earliest = minutes(req.day_start) + offsets.get(str(day.day), 0)
        origin_id = "hotel"
        for index, activity in enumerate(day.activities):
            leg = day.legs[index]
            if leg.origin_id != origin_id or leg.destination_id != activity.place_id:
                error("route_mismatch", "交通与活动衔接不匹配", activity.id)
            try:
                start, end = minutes(activity.start), minutes(activity.end)
            except ValueError:
                error("invalid_time", "活动时间格式错误", activity.id)
                continue
            if start < earliest + leg.duration_minutes + TRANSFER_BUFFER:
                error("late_arrival", "上一段交通后无法按时到达该活动", activity.id)
            if activity.day != day.day or end <= start or end > minutes(req.day_end):
                error("invalid_window", "活动时间或所属日期超出允许范围", activity.id)
            place = CATALOG.get(activity.place_id)
            if place is None:
                error("unknown_place", "活动使用了目录以外的地点", activity.id)
            elif (place.opening and start < minutes(place.opening)) or (
                place.closing and end > minutes(place.closing)
            ):
                error("opening_hours", "活动超出当前已知营业时间", activity.id)
            earliest, origin_id = end, activity.place_id
        returning = day.legs[-1]
        if returning.origin_id != origin_id or returning.destination_id != "hotel":
            error("return_mismatch", "回酒店路线缺失或不匹配")
        if earliest + returning.duration_minutes + TRANSFER_BUFFER > minutes(req.day_end):
            error("late_return", "返回酒店时间超出当日结束时间")
    visited = {a.place_id for a in all_activities}
    for place_id in req.must_visit:
        if place_id not in visited:
            error("missing_required", f"必去地点尚未排入：{CATALOG[place_id].name}")
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
