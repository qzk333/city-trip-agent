"""Goal-preserving cross-day replanning with bounded feedback and sourced discovery."""

from copy import deepcopy
from itertools import islice
from time import monotonic

from pydantic import Field

from app.lodging import trip_points
from app.planner import build_plan
from app.revisions import (
    CandidateSearch,
    model_json,
    refresh_lodging,
    revise_route,
    revision_context,
)
from app.schemas import Check, Schema
from app.tools import ToolUnavailable
from app.validation import validate_itinerary

MAX_REPAIR_ROUNDS = 3
MAX_SEARCHES = 2


class DaySelection(Schema):
    day: int = Field(ge=1, le=3)
    place_ids: list[str] = Field(max_length=20)


class ChangeReason(Schema):
    place_id: str
    reason: str = Field(min_length=1, max_length=200)


class RepairPlan(Schema):
    summary: str = Field(min_length=1, max_length=400)
    days: list[DaySelection] = Field(default_factory=list, max_length=3)
    searches: list[CandidateSearch] = Field(default_factory=list, max_length=2)
    reasons: list[ChangeReason] = Field(default_factory=list, max_length=30)


def scoped_assignment(proposal, base, scope):
    """Accept redundant unchanged frozen rows, while preserving their saved plan."""
    per_day = {item.day: list(item.place_ids) for item in proposal.days}
    if len(per_day) != len(proposal.days):
        raise ToolUnavailable("重规划重复返回了同一天，请每个日期只返回一次。")
    frozen = {day.day: [a.place_id for a in day.activities] for day in base.days if day.day not in scope}
    extras = set(per_day) - scope
    if any(day not in frozen or per_day[day] != frozen[day] for day in extras):
        raise ToolUnavailable("重规划试图修改冻结日期；请保持这些日期的地点和顺序不变。")
    if not scope <= set(per_day):
        raise ToolUnavailable(f"重规划必须完整返回允许调整的日期 {sorted(scope)}。")
    frozen_ids = {pid for ids in frozen.values() for pid in ids}
    ignored = sorted({pid for day in scope for pid in per_day[day] if pid in frozen_ids})
    assignment = {day: [pid for pid in per_day[day] if pid not in frozen_ids] for day in sorted(scope)}
    return assignment, {"unchanged_frozen_days": sorted(extras), "retained_frozen_places": ignored}


def errors(result):
    return [check.model_dump() for check in result.checks if check.severity == "error"]


def requested_days(base, intent):
    assignment = {day.day: [a.place_id for a in day.activities] for day in base.days}
    for op in intent.operations:
        if op.kind == "remove":
            assignment[op.day].remove(op.place_id)
        elif op.kind == "move":
            assignment[op.day].remove(op.place_id)
            assignment[op.target_day].append(op.place_id)
        elif op.kind == "add":
            assignment[op.day].append(op.place_id)
        elif op.kind == "reorder":
            assignment[op.day] = list(op.place_ids)
    return assignment


def scoped_days(req, intent):
    touched = set()
    for op in intent.operations:
        touched.add(1 if op.kind == "arrival" else req.days if op.kind == "departure" else op.day)
        if op.target_day:
            touched.add(op.target_day)
    touched.update(search.day for search in intent.searches)
    if any(day < 1 or day > req.days for day in intent.frozen_days):
        raise ToolUnavailable("要求保持不变的日期超出旅行范围。")
    if touched.intersection(intent.frozen_days):
        raise ToolUnavailable("要求修改的日期同时被指定保持不变，请明确本次调整范围。")
    scope = set(range(1, req.days + 1)) if intent.scope == "all_days" or not touched else set(touched)
    if intent.scope == "related_days" and touched:
        if len(touched) == 1:
            day = next(iter(touched))
            scope.update(adjacent for adjacent in (day - 1, day + 1) if 1 <= adjacent <= req.days)
        else:
            scope.update(range(min(touched), max(touched) + 1))
    return scope.difference(intent.frozen_days)


def summarize_changes(base, result, catalog, reasons):
    before = {a.place_id: a for day in base.days for a in day.activities}
    after = {a.place_id: a for day in result.days for a in day.activities}
    changes = []
    for pid in dict.fromkeys([*before, *after]):
        old, new = before.get(pid), after.get(pid)
        name = catalog[pid].name
        if old is None:
            change = f"第 {new.day} 天新增 {name}"
        elif new is None:
            change = f"移除普通推荐：{name}"
        elif old.day != new.day:
            change = f"{name} 从第 {old.day} 天移到第 {new.day} 天"
        elif old.start != new.start:
            change = f"第 {new.day} 天 {name} 调整为 {new.start} 开始"
        else:
            continue
        changes.append(change + (f"：{reasons[pid]}" if pid in reasons else ""))
    return changes[:40]


def run_revision(req, base, intent, tools, catalog, *, message, emit=lambda *_: None):
    """Return effective requirements, a preview, its catalog, and an audit trail."""
    intent = intent.model_copy(deep=True)
    catalog = dict(catalog)
    if intent.clarification:
        raise ToolUnavailable("需要补充说明：" + intent.clarification)
    scope = scoped_days(req, intent)
    if not scope:
        raise ToolUnavailable("本次没有允许调整的日期。")
    if intent.reselect and (intent.require_reselection or not intent.operations) and intent.scope == "local":
        raise ToolUnavailable("重新挑选景点需要允许协调，请选择自动协调或全程协调。")
    if intent.allow_drop_optional and not intent.reselect:
        raise ToolUnavailable("只有允许重新选择普通推荐时，才可替换未点名的活动。")
    searches = {}
    groups = []
    audit = {"rounds": [], "searches": [], "scope_days": sorted(scope)}
    reasons = {}
    base_ids = {a.place_id for day in base.days for a in day.activities}
    # Saved routes are evidence at the same coordinates, and avoid spending QPS
    # repeatedly on every model repair. Changed boundary coordinates are excluded.
    changed_endpoints = {
        op.kind
        for op in intent.operations
        if op.kind in {"arrival", "departure"} and op.location_id and op.location_id != op.kind
    }
    for day in base.days:
        for leg in day.legs:
            if not changed_endpoints.intersection({leg.origin_id, leg.destination_id}):
                tools.cache[(leg.origin_id, leg.destination_id, req.transport)] = leg
                tools.cache[(leg.origin_id, leg.destination_id, leg.mode)] = leg
    tools.guides = base.guides

    def search_more(queries):
        for query in queries:
            if query.day not in scope:
                raise ToolUnavailable("补查景点的日期超出本次调整范围。")
            key = (query.query.strip(), query.anchor_id, query.category)
            if key not in searches:
                if len(searches) >= MAX_SEARCHES:
                    raise ToolUnavailable("已达到本次 2 次景点补查预算，请缩小要求后重试。")
                points = trip_points(req, catalog)
                if query.anchor_id and query.anchor_id not in points:
                    raise ToolUnavailable("补查所参考的位置尚未确认，请指定已有景点或抵离地点。")
                emit("searching_places", f"补查第 {query.day} 天的 {query.query} 候选")
                searches[key] = []  # Failed attempts also consume the search budget.
                record = {**query.model_dump(), "place_ids": [], "status": "pending"}
                audit["searches"].append(record)
                try:
                    found = tools.search_candidates(
                        query.query.strip(), query.category, points.get(query.anchor_id)
                    )
                except ToolUnavailable:
                    record["status"] = "unavailable"
                    raise
                # Existing IDs retain their original evidence and scheduling attributes.
                searches[key] = list(found)
                for pid, place in found.items():
                    catalog.setdefault(pid, place)
                record.update(place_ids=list(found), status="ok" if found else "no_result")
            ids = searches[key]
            if query.required:
                additions = [pid for pid in ids if pid not in base_ids]
                if not additions:
                    raise ToolUnavailable(f"未查到 {query.query} 的可核验候选，不能完成本次新增要求。")
                group = {"day": query.day, "place_ids": additions}
                if group not in groups:
                    groups.append(group)

    search_more(intent.searches)
    for op in intent.operations:
        if op.kind == "remove" and not intent.allow_drop_optional and op.place_id in catalog:
            alias = catalog[op.place_id].name.split("·")[0].strip()
            if alias not in message:
                raise ToolUnavailable("普通日期协调不能自行移除未点名的活动；如需替换，请允许重新挑选。")
        if op.place_query:
            if op.kind != "add" or op.place_id:
                raise ToolUnavailable("新地点搜索只用于明确新增，不能替代已经确认的地点 ID。")
            matching_searches = [
                search
                for search in intent.searches
                if search.query == op.place_query and search.day == op.day
            ]
            if not matching_searches:
                query = CandidateSearch(query=op.place_query, day=op.day)
                search_more([query])
                matching_searches = [query]
            pool = set(
                pid
                for query in matching_searches
                for pid in searches[(query.query.strip(), query.anchor_id, query.category)]
            )
            exact = [pid for pid in pool if catalog[pid].name == op.place_query]
            matches = exact or [pid for pid in pool if op.place_query in catalog[pid].name]
            if len(matches) != 1:
                names = "、".join(catalog[pid].name for pid in matches[:3])
                raise ToolUnavailable(
                    "新增地点未找到唯一匹配，请使用准确名称"
                    + (f"；候选为：{names}" if names else "或更换关键词。")
                )
            op.place_id, op.place_query = matches[0], None

    if intent.operations:
        updated, result = revise_route(req, base, intent, tools, catalog, user_message=message)
        desired = requested_days(base, intent)
        # Indoor replacement is intentionally allowed by this explicit request.
        for op in intent.operations:
            if op.kind == "indoor":
                desired[op.day] = [a.place_id for d in result.days if d.day == op.day for a in d.activities]
    elif intent.reselect or intent.searches:
        updated, result = req.model_copy(deep=True), base.model_copy(deep=True)
        desired = requested_days(base, intent)
    else:
        raise ToolUnavailable("没有识别到可执行的调整目标，请补充日期、地点或希望改变的安排。")
    required = (
        set(updated.must_visit)
        | set(updated.place_days)
        | {a.place_id for d in base.days for a in d.activities if a.locked}
    )
    if not intent.allow_drop_optional:
        required.update(pid for ids in desired.values() for pid in ids)
    excluded = {op.place_id for op in intent.operations if op.kind == "remove"}
    original_ids = {a.place_id for day in base.days for a in day.activities}
    discovered_ids = {pid for ids in searches.values() for pid in ids}
    explicit_additions = {op.place_id for op in intent.operations if op.kind == "add"}

    def check_goals(candidate):
        candidate.checks = [c for c in candidate.checks if c.code != "revision_goal_missing"]
        visited = {a.place_id for day in candidate.days for a in day.activities}
        missing = required - visited
        if missing:
            candidate.checks.append(
                Check(
                    code="revision_goal_missing",
                    severity="error",
                    message="调整未保留必须满足的地点："
                    + "、".join(catalog[pid].name for pid in sorted(missing)),
                )
            )
        for group in groups:
            if not any(
                day.day == group["day"] and any(a.place_id in group["place_ids"] for a in day.activities)
                for day in candidate.days
            ):
                candidate.checks.append(
                    Check(
                        code="revision_goal_missing",
                        severity="error",
                        message=f"第 {group['day']} 天尚未加入本次要求补查的新地点。",
                    )
                )
        return candidate

    result = check_goals(result)
    audit["rounds"].append({"stage": "initial", "errors": errors(result)})
    needs_reselect = intent.require_reselection or bool(groups) or not intent.operations
    verified = None
    used_fallback = False
    if errors(result) and not needs_reselect and intent.scope != "local" and len(scope) > 1:
        try:
            support_base = base.model_copy(deep=True)
            support_base.day_start_offsets = result.day_start_offsets
            support_plan, support_result = demo_rebalance(
                desired,
                scope,
                required,
                updated,
                support_base,
                tools,
                catalog,
                excluded,
                0,
                max_trials=4,
                demo=False,
                with_result=True,
            )
            verified = (support_plan, support_result)
            audit["program_verified_option"] = support_plan.model_dump(mode="json")
        except ToolUnavailable as exc:
            audit["precheck_note"] = str(exc)
    if (errors(result) or needs_reselect) and intent.scope != "local":
        feedback = errors(result)
        for round_number in range(1, MAX_REPAIR_ROUNDS + 1):
            if tools.deadline - monotonic() < 5 or tools.route_calls >= tools.settings.max_route_calls:
                audit["stop_reason"] = "budget"
                break
            proposal = None
            try:
                emit("replanning", f"第 {round_number} 轮协调：结合时间、住宿和冲突重新分配活动")
                if tools.settings.planning_mode == "llm":
                    proposal = model_json(
                        tools.settings,
                        "你是旅行主 Agent 的重规划器。"
                        "根据用户目标、已选酒店、真实候选和程序反馈重新分配景点。"
                        "只返回允许调整日期的完整 days/place_ids，不得编造ID、坐标、营业、交通或时长。"
                        "place_days、必去、锁定预约、酒店及抵离条件是硬约束；不能更改。"
                        "只有place_days和预约限制日期，required_place_ids只要求保留，不限制原先日期。"
                        "frozen_place_ids已由程序保留，不要重复放进允许调整的日期。"
                        "program_verified_option如非空，是程序已检查可行的跨日分配，优先采用；勿重复原来失败分组。"
                        "允许把其他普通活动从拥挤日移到空闲日；原分组和顺序仅是偏好。"
                        "required_place_ids必须全部保留，excluded_place_ids不能安排。未允许替换时不得删景点。"
                        "可替换时优先减少折返、控制每日负担，别用咖啡馆大量填充景点。"
                        "必须包含required_search_groups指定日的至少一个候选。"
                        "完整考虑首尾酒店交通、停留、午餐、衔接、营业和返程。按地理邻近安排顺序。"
                        "现有候选不足且允许重选时可以只返回searches、暂不返回days，最多补查2次，每次5条。"
                        "用户明确新增的地点不可被换成其他景点；无法满足则解释原因，不能悄悄丢失要求。"
                        "summary解释本轮取舍，reasons逐项解释地点移动或替换的原因。",
                        {
                            **revision_context(updated, base, catalog),
                            "user_request": message,
                            "allowed_days": sorted(scope),
                            "requested_assignment": desired,
                            "required_place_ids": sorted(
                                required
                                - {a.place_id for d in base.days if d.day not in scope for a in d.activities}
                            ),
                            "frozen_place_ids": [
                                a.place_id for d in base.days if d.day not in scope for a in d.activities
                            ],
                            "program_verified_option": verified[0].model_dump(mode="json")
                            if verified
                            else None,
                            "excluded_place_ids": sorted(excluded),
                            "required_search_groups": groups,
                            "allow_reselect": intent.reselect,
                            "remaining_searches": MAX_SEARCHES - len(searches),
                            "last_attempt": [d.model_dump(mode="json") for d in result.days],
                            "feedback": feedback,
                        },
                        RepairPlan,
                        tools,
                    )
                else:
                    proposal = demo_rebalance(
                        desired, scope, required, updated, base, tools, catalog, excluded, round_number
                    )
                if proposal.searches:
                    if not intent.reselect:
                        raise ToolUnavailable("本次仅协调日期，不允许自行补查替换地点。")
                    search_more(proposal.searches)
                    discovered_ids = {pid for ids in searches.values() for pid in ids}
                    if not proposal.days:
                        feedback = [{"message": "已补查真实候选，请使用最新目录返回完整日期分配。"}]
                        audit["rounds"].append({"stage": "search", "round": round_number})
                        continue
                per_day, normalization = scoped_assignment(proposal, base, scope)
                frozen_ids = {a.place_id for day in base.days if day.day not in scope for a in day.activities}
                flat = [pid for ids in per_day.values() for pid in ids]
                if len(flat) != len(set(flat)) or any(
                    pid not in catalog or pid in excluded or pid in frozen_ids for pid in flat
                ):
                    raise ToolUnavailable("重规划包含未知、重复、已移除或冻结日期的地点。")
                if not intent.reselect and set(flat) - original_ids - explicit_additions - discovered_ids:
                    raise ToolUnavailable("本次未要求重选景点，请保留原活动，只协调其日期和顺序。")
                all_visited = set(flat) | frozen_ids
                if not required <= all_visited:
                    missing = required - all_visited
                    raise ToolUnavailable(
                        "重规划遗漏必须保留的地点："
                        + "、".join(f"{catalog[pid].name}({pid})" for pid in sorted(missing))
                    )
                for pid, assigned in updated.place_days.items():
                    if assigned in scope and pid not in per_day[assigned]:
                        raise ToolUnavailable(f"{catalog[pid].name} 必须保留在用户指定的第 {assigned} 天。")
                locks = [a.model_dump() for day in base.days for a in day.activities if a.locked]
                for locked in locks:
                    if locked["day"] in scope and locked["place_id"] not in per_day[locked["day"]]:
                        raise ToolUnavailable("重规划不能移动锁定活动或预约。")
                options = {
                    "per_day_ids": {str(day): ids for day, ids in per_day.items()},
                    "preferred": flat,
                    "fixed": locks,
                    "delays": result.day_start_offsets,
                    "frozen_days": [d.model_dump() for d in base.days if d.day not in scope],
                    "lodging": base.lodging.model_dump(mode="json"),
                    "weather_snapshot": base.weather.model_dump(mode="json"),
                }
                candidate = build_plan(updated, flat, tools, options, catalog)
                candidate = validate_itinerary(updated, candidate, options, catalog)
                for day in candidate.days:
                    if day.day in scope and {a.place_id for a in day.activities} != set(per_day[day.day]):
                        candidate.checks.append(
                            Check(
                                code="revision_incomplete",
                                severity="error",
                                message=f"第 {day.day} 天未能排入本轮分配的全部活动，"
                                "请继续跨日协调或更换普通推荐。",
                            )
                        )
                result = check_goals(candidate)
                result.revision_rounds = round_number
                feedback = errors(result)
                audit["rounds"].append(
                    {
                        "stage": "planned",
                        "round": round_number,
                        "summary": proposal.summary,
                        "days": {str(d): ids for d, ids in per_day.items()},
                        "scope_handling": normalization,
                        "errors": feedback,
                    }
                )
                if not feedback:
                    reasons = {
                        reason.place_id: reason.reason
                        for reason in proposal.reasons
                        if reason.place_id in catalog
                    }
                    result.revision_note = proposal.summary
                    break
            except ToolUnavailable as exc:
                feedback = [{"message": str(exc)}]
                audit["rounds"].append(
                    {
                        "stage": "rejected",
                        "round": round_number,
                        "message": str(exc),
                        "proposal": proposal.model_dump(mode="json") if proposal else None,
                    }
                )
                if exc.fatal:
                    break
        else:
            audit["stop_reason"] = "round_limit"
        if needs_reselect and not any(
            row.get("stage") == "planned" and not row.get("errors") for row in audit["rounds"]
        ):
            result.checks.append(
                Check(
                    code="revision_goal_missing",
                    severity="error",
                    message="未能在本次预算内完成重新选景点的要求，请缩小范围或调整条件。",
                )
            )
    elif needs_reselect and intent.scope == "local":
        result.checks.append(
            Check(
                code="revision_goal_missing",
                severity="error",
                message="重新挑选景点需要允许协调日期，请选择自动协调或全程协调。",
            )
        )
    if errors(result) and verified:
        support_plan, result = verified
        used_fallback = True
        reasons = {reason.place_id: reason.reason for reason in support_plan.reasons}
        result.revision_note = "模型建议未通过本次检查，提供程序已校验的跨日协调预览：" + support_plan.summary
        audit["fallback"] = "program_verified"
    refresh_lodging(updated, result, catalog)
    result = check_goals(result)
    # The final check must not clear a failure to execute a requested reselection.
    if needs_reselect and not any(
        row.get("stage") == "planned" and not row.get("errors") for row in audit["rounds"]
    ):
        result.checks.append(
            Check(
                code="reselection_incomplete",
                severity="error",
                message="本次重新挑选尚未完成，不能作为成功调整保存。",
            )
        )
    result.revision_note = result.revision_note or intent.summary
    result.revision_rounds = max((row.get("round", 0) for row in audit["rounds"]), default=0)
    if errors(result) and len(audit["rounds"]) > 1:
        result.checks.append(
            Check(
                code="replanning_limit",
                severity="warning",
                message=f"已执行 {result.revision_rounds} 轮有限协调，仍未满足全部条件；"
                "保留错误预览，请调整要求后重试。",
            )
        )
    result.revision_status = (
        "unavailable"
        if used_fallback
        else "model_reviewed"
        if tools.settings.planning_mode == "llm"
        else "rules_only"
    )
    result.revision_changes = summarize_changes(base, result, catalog, reasons)
    result.trace = [
        *base.trace,
        *tools.trace,
        {
            "tool": "revision_replanning",
            "status": "valid" if not errors(result) else "incomplete",
            "rounds": len(audit["rounds"]) - 1,
            "searches": len(searches),
        },
    ]
    result = validate_itinerary(updated, result, catalog=catalog)
    audit["resolved_intent"] = intent.model_dump(mode="json")
    audit["route_calls"] = tools.route_calls
    audit["model_requests"] = sum(row.get("tool") == "revision_model" for row in tools.trace)
    usage = [
        row["total_tokens"]
        for row in tools.trace
        if row.get("tool") == "revision_model" and "total_tokens" in row
    ]
    audit["reported_total_tokens"] = sum(usage) if usage else None
    return updated, result, catalog, audit


def demo_rebalance(
    desired,
    scope,
    required,
    req,
    base,
    tools,
    catalog,
    excluded,
    round_number,
    *,
    max_trials=12,
    demo=True,
    with_result=False,
):
    """Small deterministic demo search, explicitly not a model invocation."""
    protected = set(req.place_days) | {a.place_id for d in base.days for a in d.activities if a.locked}
    choices = []
    for source in sorted(scope, key=lambda d: -len(desired[d])):
        for target in sorted(scope, key=lambda d: len(desired[d])):
            if target == source:
                continue
            for pid in reversed(desired[source]):
                if pid not in protected:
                    assignment = deepcopy(desired)
                    assignment[source].remove(pid)
                    assignment[target].append(pid)
                    choices.append((assignment, pid, source, target))
    for assignment, pid, source, target in islice(choices, max_trials):
        flat = [pid for day in scope for pid in assignment[day]]
        options = {
            "per_day_ids": {str(d): assignment[d] for d in scope},
            "preferred": flat,
            "fixed": [a.model_dump() for d in base.days for a in d.activities if a.locked],
            "delays": base.day_start_offsets,
            "frozen_days": [d.model_dump() for d in base.days if d.day not in scope],
            "lodging": base.lodging.model_dump(mode="json"),
            "weather_snapshot": base.weather.model_dump(mode="json"),
        }
        result = validate_itinerary(req, build_plan(req, flat, tools, options, catalog), options, catalog)
        if not errors(result) and all(
            {a.place_id for a in day.activities} == set(assignment[day.day])
            for day in result.days
            if day.day in scope
        ):
            proposal = RepairPlan(
                summary=f"{'演示规则' if demo else '程序预检建议'}将 {catalog[pid].name} "
                f"从第 {source} 天移至第 {target} 天，"
                "腾出时间保留你的指定安排。",
                days=[DaySelection(day=d, place_ids=assignment[d]) for d in sorted(scope)],
                reasons=[ChangeReason(place_id=pid, reason="为拥挤日期腾出时间；已通过路线和时间预检。")],
            )
            return (proposal, result) if with_result else proposal
    raise ToolUnavailable("演示规则在本次有限搜索中未找到跨日安排，可启用模型或减少活动。")
