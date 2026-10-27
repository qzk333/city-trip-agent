"""Validate a complete planning response before committing any parsed decisions."""

import json
import re
from collections import defaultdict

from pydantic import ValidationError

from app.schemas import RepairScheduledDay, ScheduledDay


class PlanningOutputError(ValueError):
    def __init__(self, issues):
        self.issues = issues[:8]
        super().__init__("模型规划输出未通过校验")


def problem(code, field, message):
    raise PlanningOutputError([{"code": code, "field": field, "message": message}])


def schema_issues(exc, index):
    labels = {
        "extra_forbidden": "返回了时间表 schema 未允许的字段。",
        "string_pattern_mismatch": "时间必须为当地 HH:MM，小时 00—23、分钟 00—59。",
        "enum": "请使用 schema 列出的枚举值。",
        "missing": "缺少必填字段。",
        "value_error": "结束必须晚于开始，可调整窗口也必须按时间递增。",
    }
    rows = []
    for error in exc.errors(include_input=False)[:8]:
        path = [
            str(part)
            if isinstance(part, int) or re.fullmatch(r"[a-z_]{1,40}", str(part))
            else "unknown_field"
            for part in error["loc"]
        ]
        rows.append(
            {
                "code": "schedule_schema",
                "field": ".".join(["daily_schedule", str(index), *path])[:160],
                "message": labels.get(error["type"], "字段类型、长度或取值不符合时间表 schema。"),
            }
        )
    return rows


def selection_ledger(days, catalog):
    """Make cross-day use and remaining choices explicit, without choosing for the model."""
    used = defaultdict(list)
    for index, day in enumerate(days if isinstance(days, list) else []):
        if not isinstance(day, dict) or not isinstance(day.get("activities"), list):
            continue
        for offset, stop in enumerate(day["activities"]):
            pid = stop.get("place_id") if isinstance(stop, dict) else None
            if isinstance(pid, str) and pid in catalog:
                used[pid].append({"day": day.get("day", index + 1), "position": offset + 1})
    return {
        "used": [
            {"place_id": pid, "name": catalog[pid].name, "occurrences": rows} for pid, rows in used.items()
        ],
        "unused": [{"place_id": pid, "name": p.name} for pid, p in catalog.items() if pid not in used],
        "policy": "已用与未用候选供整体比较；允许不同时间或目的的重访，须解释价值，避免无意义凑数。",
    }


def parse_planning_output(raw, req, catalog, *, require_schedule=False):
    if isinstance(raw, str):
        fenced = re.fullmatch(r"\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*", raw)
        if fenced:
            raw = fenced.group(1)
    try:
        content = json.loads(raw)
    except (ValueError, TypeError):
        problem("invalid_json", "response", "请返回单个有效 JSON 对象，不要附加 Markdown 或解释。")
    if not isinstance(content, dict):
        problem("invalid_root", "response", "根节点必须是 JSON 对象。")

    schedule = content.get("daily_schedule")
    parsed = None
    if require_schedule and schedule is None:
        problem("missing_schedule", "daily_schedule", "本次修改必须返回完整具体时间表，不能只返回地点列表。")
    if schedule is not None:
        if not isinstance(schedule, list) or len(schedule) != req.days:
            problem("day_count", "daily_schedule", f"完整时间表必须包含 {req.days} 天。")
        parsed = []
        for index, day in enumerate(schedule):
            try:
                if isinstance(day, dict):
                    for key in ("activities", "daytime_activities", "evening_activities", "breaks"):
                        for block in day.get(key, []) if isinstance(day.get(key, []), list) else []:
                            if not isinstance(block, dict):
                                continue
                            for field in ("start", "end", "earliest_start", "latest_end"):
                                value = block.get(field)
                                match = (
                                    re.fullmatch(r"(\d{1,2}):(\d{2})(?::00)?", value)
                                    if isinstance(value, str)
                                    else None
                                )
                                if match and int(match[1]) < 24 and int(match[2]) < 60:
                                    block[field] = f"{int(match[1]):02d}:{match[2]}"
                if isinstance(day, dict) and ("evening_activities" in day or "daytime_activities" in day):
                    blocks = RepairScheduledDay.model_validate(day)
                    activities = sorted(
                        [*blocks.daytime_activities, *blocks.evening_activities], key=lambda a: a.start
                    )
                    day = {
                        "day": blocks.day,
                        "activities": [a.model_dump() for a in activities],
                        "breaks": [b.model_dump() for b in blocks.breaks],
                        "weather_note": blocks.weather_note[:240]
                        + "；晚间取舍："
                        + blocks.evening_explanation[:240],
                    }
                parsed.append(ScheduledDay.model_validate(day))
            except ValidationError as exc:
                raise PlanningOutputError(schema_issues(exc, index)) from exc
        if [day.day for day in parsed] != list(range(1, req.days + 1)):
            problem("day_order", "daily_schedule", "日期必须按旅行天数从 1 连续排列，不重复或遗漏。")
        unknown = []
        for index, day in enumerate(parsed):
            for offset, stop in enumerate(day.activities):
                field = f"daily_schedule.{index}.activities.{offset}.place_id"
                if stop.place_id not in catalog:
                    unknown.append(
                        {
                            "code": "unknown_place",
                            "field": field,
                            "message": "地点不在已确认候选目录；请选择目录中的真实 ID。",
                        }
                    )
        if unknown:
            raise PlanningOutputError(unknown)
        scheduled_ids = [stop.place_id for day in parsed for stop in day.activities]
        # A valid concrete timetable is authoritative. Redundant indexes must not
        # discard it or force the model to make the same planning decision twice.
        supplied = content.get("place_ids", [])
        alternatives = (
            [pid for pid in supplied if isinstance(pid, str) and pid in catalog]
            if isinstance(supplied, list)
            else []
        )
        content["place_ids"] = list(dict.fromkeys([*scheduled_ids, *alternatives]))
        content["day_groups"] = [[stop.place_id for stop in day.activities] for day in parsed]
        content["daily_schedule"] = [day.model_dump() for day in parsed]
        return content

    order = content.get("place_ids")
    if isinstance(order, list) and all(isinstance(pid, str) for pid in order):
        order = list(dict.fromkeys(order))
        content["place_ids"] = order
    if not isinstance(order, list) or (not order and parsed is None) or len(order) > len(catalog):
        problem(
            "candidate_index",
            "place_ids",
            "需要非空候选 ID 列表，长度不能超出目录；有时间表时可省略此冗余字段。",
        )
    if any(not isinstance(pid, str) or pid not in catalog for pid in order):
        problem("unknown_place", "place_ids", "候选列表包含未知 ID 或非字符串；只能使用目录 ID。")

    groups = content.get("day_groups")
    if groups is not None:
        if isinstance(groups, list) and groups and all(isinstance(group, dict) for group in groups):
            if [group.get("day") for group in groups] != list(range(1, req.days + 1)):
                problem("day_order", "day_groups", "分天索引必须完整覆盖旅行日期并按日期递增。")
            groups = [group.get("place_ids") for group in groups]
        if (
            not isinstance(groups, list)
            or len(groups) != req.days
            or not all(isinstance(group, list) for group in groups)
        ):
            problem("day_groups", "day_groups", "分天索引必须是每日 ID 数组；有具体时间表时可省略。")
        flat = [pid for group in groups for pid in group]
        if any(not isinstance(pid, str) or pid not in catalog for pid in flat):
            problem("unknown_place", "day_groups", "分天索引含目录外或无效 ID。")
        if not set(flat) <= set(order):
            problem(
                "index_disagreement", "day_groups", "分天索引包含未在候选索引中的 ID，请纠正或省略冗余索引。"
            )
    if groups is not None:
        content["day_groups"] = groups
    return content
