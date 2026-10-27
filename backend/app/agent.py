from time import monotonic
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent_contracts import GuideBrief
from app.agent_tasks import TaskJournal
from app.candidate_ranking import matches_themes
from app.catalog import CATALOG
from app.config import Settings
from app.guide_agent import analyze_guides, guide_task_input
from app.guide_discovery import admit_guide_candidates, expand_guide_candidates
from app.hotel_agent import hotel_task_input, research_hotels
from app.llm import model_candidates
from app.planner import build_plan
from app.planning_context import weather_context
from app.research import research_guides
from app.review_agent import REVIEW_CODES, audit_schedule
from app.schedule_review import QUALITY_CODES
from app.schemas import (
    Check,
    Itinerary,
    Place,
    PlaceRecommendation,
    PlanningDecision,
    Requirements,
    ReviewFailure,
    ToolResult,
)
from app.spatial_planning import group_candidates
from app.tools import ToolUnavailable, TravelTools
from app.travel_profile import normalize_profile
from app.validation import validate_itinerary


class PlanState(TypedDict, total=False):
    requirements: Requirements
    order: list[str]
    itinerary: Itinerary
    guide_brief: GuideBrief
    weather: ToolResult


def generate(
    requirements: Requirements,
    settings: Settings,
    emit,
    overrides: dict | None = None,
    catalog: dict[str, Place] | None = None,
    journal: TaskJournal | None = None,
    catalog_updates: dict[str, Place] | None = None,
) -> Itinerary:
    catalog = dict(catalog if catalog is not None else CATALOG)
    demand_planning = not all(p.fixture for p in catalog.values())
    journal = journal or TaskJournal()
    tools = TravelTools(
        settings, requirements.city, requirements.country_code, province=requirements.province
    )
    recommendations: dict = {}
    review_feedback: list[dict] = []
    previous_schedule: list[dict] = []

    def weather(state: PlanState):
        emit("checking_weather", "先查询实际旅行日期的天气，未覆盖日期保持未知，再提交主 Agent 规划")
        snapshot = (
            ToolResult.model_validate(overrides["weather_snapshot"])
            if overrides and "weather_snapshot" in overrides
            else tools.weather(requirements.start_date, requirements.days)
        )
        return {"weather": snapshot}

    def research(state: PlanState):
        nonlocal catalog
        task_input = guide_task_input(requirements, catalog)
        emit("researching", "攻略 Agent 独立检索与分析资料，向主 Agent 提交带来源的研究结论")
        with journal.task("guide", task_input) as task:
            evidence = research_guides(requirements, settings, tools.deadline)
            result = analyze_guides(task_input, evidence, settings, tools.deadline)
            if not overrides:
                catalog, result = expand_guide_candidates(requirements, result, tools, catalog)
            if catalog_updates is not None:
                catalog_updates.update(catalog)
            task.complete(result.model_dump(mode="json"), status=result.task_status)
        tools.guides = result.research
        tools.trace.extend(result.trace)
        if result.research.discovery_note:
            emit("expanding_candidates", result.research.discovery_note)
        if demand_planning and not overrides:
            catalog = admit_guide_candidates(
                requirements,
                catalog,
                result.brief,
                limit=40 if settings.planning_mode == "llm" else requirements.days * 8,
            )
        emit("researching", result.brief.summary)
        return {"guide_brief": result.brief}

    def candidates(state: PlanState):
        emit("selecting", "整理需求与候选地点")
        if settings.planning_mode == "llm" and not overrides:
            order = model_candidates(
                state["requirements"],
                settings,
                tools.trace,
                emit,
                tools.deadline,
                catalog,
                tools.guides,
                recommendations,
                guide_brief=state["guide_brief"],
                weather=state["weather"],
                feedback=review_feedback,
                previous_schedule=previous_schedule,
                repair_format=True,
            )
        else:
            order = (
                list(catalog)
                if demand_planning
                else sorted(
                    catalog,
                    key=lambda p: (
                        not matches_themes(
                            catalog[p], normalize_profile(requirements.profile, requirements.note)
                        )
                    ),
                )
            )
        return {"order": order}

    def plan(state: PlanState):
        emit("planning", "查询交通并安排日程")
        tools.guides.advice = recommendations.get("transport_advice", [])
        options = dict(overrides or {})
        options["weather_snapshot"] = state["weather"].model_dump(mode="json")
        if recommendations.get("daily_schedule") is not None and not overrides:
            options["model_schedule"] = recommendations["daily_schedule"]
        if recommendations.get("day_groups") and not overrides:
            options["day_groups"] = recommendations["day_groups"]
        model_groups = recommendations.get("day_groups")
        if not overrides and model_groups is not None:
            options["per_day_ids"] = {str(i + 1): ids for i, ids in enumerate(model_groups)}
            options["day_groups"] = model_groups
            options["allowed_place_ids"] = list(
                dict.fromkeys(
                    [pid for group in model_groups for pid in group]
                    + list(requirements.must_visit)
                    + list(requirements.place_days)
                    + [a.place_id for a in requirements.appointments]
                )
            )
            options["preferred"] = [pid for group in model_groups for pid in group]
        elif demand_planning and not overrides:
            # Spatial grouping is a fallback when the model did not provide day choices.
            options["day_groups"] = group_candidates(requirements, catalog, state["order"])
            options["allowed_place_ids"] = list(catalog)
            options["preferred"] = [pid for group in options["day_groups"] for pid in group]
        if options.get("day_groups") is not None and not overrides:
            recommendations["spatial_groups"] = options["day_groups"]
            tools.trace.append(
                {
                    "tool": "spatial_selection",
                    "status": "ok",
                    "strategy": "model_days_preserved" if model_groups is not None else "demand-v1",
                    "day_groups": options["day_groups"],
                    "allowed_place_ids": options.get("allowed_place_ids", list(catalog)),
                    "model_visible_place_ids": list(catalog),
                }
            )
        itinerary = build_plan(state["requirements"], state["order"], tools, options, catalog)
        actual = {a.place_id for day in itinerary.days for a in day.activities}
        if model_groups is not None and not overrides:
            missing = set(pid for group in model_groups for pid in group) - actual
            if missing:
                itinerary.checks.append(
                    Check(
                        code="model_plan_incomplete",
                        severity="error",
                        message="模型所选地点在时间/交通校验后未能完整排入："
                        + "、".join(catalog[pid].name for pid in sorted(missing))
                        + "。本次不能作为成功规划保存，请调整要求或重新规划。",
                    )
                )
        reasons = list(recommendations.get("place_reasons", []))
        explained = {r.place_id for r in reasons}
        for pid in sorted(actual - explained):
            place = catalog[pid]
            reason = (
                "保留你明确指定的地点"
                if pid in requirements.must_visit
                else (
                    "；".join(place.candidate_match.reasons)
                    if place.candidate_match
                    else "来自当前地点目录；具体适合程度仍需确认"
                )
            )
            reasons.append(
                PlaceRecommendation(
                    place_id=pid,
                    reason=reason,
                    basis="rule",
                    tradeoff="模型未单独提供此地点理由；营业、预约与内部步行仍需核实。",
                )
            )
        timed = recommendations.get("daily_schedule") is not None and not overrides
        notes = (
            ["模型提出具体活动时间、用餐休息及天气取舍；程序保留时段并独立核算交通和硬条件。"]
            if timed
            else (
                ["模型负责取舍与分天；程序核算交通、时间和硬条件。"]
                if model_groups is not None
                else [
                    "模型仅返回候选顺序，按天分组由程序完成。"
                    if settings.planning_mode == "llm"
                    else "本次使用规则规划，没有请求主规划模型。"
                ]
            )
        )
        if settings.planning_mode == "llm" and not overrides:
            forecast_days = sum(
                day["status"] == "forecast" for day in weather_context(requirements, state["weather"])["days"]
            )
            notes.append(
                f"主模型已收到天气输入：{forecast_days}/{requirements.days} 天有逐日预报；其余日期天气未知。"
            )
            if not timed:
                notes.append("本次模型未返回具体时间表，兼容使用程序顺排；尚未实现模型时段意图。")
        if overrides:
            notes = ["本次按指定操作进行局部排程，主规划模型没有重新分天。"]
        itinerary.planning_decision = PlanningDecision(
            source="model_schedule"
            if timed
            else "model_days"
            if model_groups is not None
            else "model_order"
            if settings.planning_mode == "llm" and not overrides
            else "rules",
            model_calls=sum(t.get("tool") == "llm_candidates" for t in tools.trace),
            notes=notes,
            recommendations=reasons[:40],
            weather_provided=settings.planning_mode == "llm" and not overrides,
            place_assessments=recommendations.get("place_assessments", []),
        )
        return {"itinerary": itinerary}

    def validate(state: PlanState):
        emit("validating", "独立检查时间、必去地点与预约")
        result = validate_itinerary(state["requirements"], state["itinerary"], overrides or {}, catalog)
        return {"itinerary": result}

    def main(state: PlanState):
        nonlocal review_feedback, previous_schedule
        with journal.task(
            "main",
            {
                "requirements": requirements.model_dump(mode="json"),
                "guide_brief": state["guide_brief"].model_dump(mode="json"),
                "overrides": overrides or {},
                "weather": weather_context(requirements, state["weather"]),
            },
        ) as task:
            attempts = []
            best = None
            best_score = None
            rounds = 0
            best_groups = []
            best_attempt = 1
            for attempt in range(2):
                if attempt:
                    rounds = 1
                    emit(
                        "reviewing_schedule",
                        "将完整行程及整体审核意见反馈主 Agent，综合调整全天安排",
                    )
                    recommendations.clear()
                try:
                    selected = candidates(state)
                    if attempt and recommendations.get("daily_schedule") is None:
                        raise ToolUnavailable("复核模型未返回具体时间表，保留原预览。")
                    planned = plan({**state, **selected})
                    validated = validate({**state, **selected, **planned})
                except ToolUnavailable as exc:
                    if not attempt or best is None:
                        raise
                    code = exc.code
                    if code == "tool_unavailable":
                        code = (
                            "time_budget"
                            if "时间预算" in str(exc)
                            else "invalid_schedule"
                            if any(
                                word in str(exc) for word in ("时间表", "时间安排", "JSON", "输出", "分天")
                            )
                            else "review_failed"
                        )
                    labels = {
                        "timeout": "模型响应超时",
                        "time_budget": "本轮时间预算耗尽",
                        "invalid_schedule": "模型时间表格式或内容未通过校验",
                        "rate_limited": "模型服务限流",
                        "provider_unavailable": "模型服务暂不可用",
                        "provider_rejected": "模型请求被服务商拒绝",
                        "connection_error": "模型服务连接失败",
                        "review_failed": "未取得可校验的复核结果",
                    }
                    failure = ReviewFailure(
                        code=code,
                        message=labels.get(code, labels["review_failed"]),
                        retryable=code
                        in {"timeout", "rate_limited", "provider_unavailable", "connection_error"},
                        details=exc.details,
                    )
                    best["itinerary"].planning_decision.review_failure = failure
                    best["itinerary"].checks.append(
                        Check(
                            code="schedule_review_unavailable",
                            severity="warning",
                            message=f"时间表复核未完成：{failure.message}。保留原预览及具体问题；这不代表旅行条件无解。",
                        )
                    )
                    attempts.append(
                        {"attempt": attempt + 1, "status": "unavailable", "failure": failure.model_dump()}
                    )
                    break
                draft = validated["itinerary"]
                if not overrides:
                    audit_schedule(
                        requirements,
                        draft,
                        catalog,
                        tools,
                        journal=journal,
                        emit=emit,
                        reserve_seconds=25 if not attempt else 0,
                    )
                    draft = validate_itinerary(requirements, draft, {}, catalog)
                    validated["itinerary"] = draft
                hard = [c for c in draft.checks if c.severity == "error"]
                soft = [
                    c
                    for c in draft.checks
                    if c.code in (QUALITY_CODES - {"weather_shelter_unknown"}) | REVIEW_CODES
                ]
                report = draft.planning_decision.quality_review
                reviewed = report.status in {"passed", "changes_requested"}
                # Compare complete itineraries using the reviewer's holistic judgment.
                # No invented penalty from issue counts, periods or activity quantity.
                quality = report.overall_score
                score = (len(hard), -quality if quality is not None else 0)
                attempts.append(
                    {
                        "attempt": attempt + 1,
                        "status": draft.status,
                        "issues": [c.model_dump() for c in [*hard, *soft]],
                        "schedule": recommendations.get("daily_schedule", []),
                        "timetable": [
                            {
                                "day": day.day,
                                "activities": [
                                    a.model_dump(include={"place_id", "start", "end"}) for a in day.activities
                                ],
                                "breaks": [
                                    b.model_dump(include={"kind", "start", "end"}) for b in day.breaks
                                ],
                            }
                            for day in draft.days
                        ],
                        "quality_review": draft.planning_decision.quality_review.model_dump(mode="json"),
                    }
                )
                if (
                    best is None
                    or len(hard) < best_score[0]
                    or (
                        attempt
                        and len(hard) == best_score[0]
                        and (
                            not reviewed
                            or quality is None
                            or best["itinerary"].planning_decision.quality_review.overall_score is None
                            or best["itinerary"].planning_decision.quality_review.status == "unavailable"
                            or score <= best_score
                        )
                    )
                ):
                    best = {**selected, **validated}
                    best_score = score
                    best_groups = recommendations.get("spatial_groups", recommendations.get("day_groups", []))
                    best_attempt = attempt + 1
                if not (
                    recommendations.get("daily_schedule") is not None
                    and (hard or soft)
                    and not overrides
                    and monotonic() < tools.deadline
                ):
                    break
                review_feedback = [c.model_dump() for c in [*hard, *soft]]
                if draft.planning_decision.quality_review.status == "changes_requested":
                    review_feedback.append(
                        {
                            "code": "reviewer_report",
                            "report": draft.planning_decision.quality_review.model_dump(mode="json"),
                        }
                    )
                previous_schedule = [d.model_dump(mode="json") for d in draft.days]
            draft = best["itinerary"]
            draft.planning_decision.review_rounds = rounds
            draft.planning_decision.selected_attempt = best_attempt
            if rounds:
                draft.planning_decision.review_outcome = (
                    "failed"
                    if draft.planning_decision.review_failure
                    else "applied"
                    if best_attempt > 1 and attempts[0].get("timetable") != attempts[-1].get("timetable")
                    else "unchanged"
                )
            elif (hard or soft) and recommendations.get("daily_schedule") is not None and not overrides:
                draft.planning_decision.review_outcome = "budget_exhausted"
            draft.planning_decision.model_calls = sum(t.get("tool") == "llm_candidates" for t in tools.trace)
            if rounds:
                draft.planning_decision.notes.append(
                    f"时间表复核未完成：{draft.planning_decision.review_failure.message}，保留草案及待解决问题。"
                    if draft.planning_decision.review_failure
                    else "已进行一次有界整体复核，先保障时间与用户要求，再依据审核对全天体验的评价选版；"
                    "剩余问题见行程检查。"
                )
            tools.trace.append(
                {
                    "tool": "schedule_review",
                    "status": "reviewed" if rounds else "not_needed",
                    "attempts": len(attempts),
                }
            )
            task.complete(
                {
                    "place_ids": best["order"],
                    "day_groups": best_groups,
                    "schedule_attempts": attempts,
                    "itinerary_status": draft.status,
                    "days": [day.model_dump(mode="json") for day in draft.days],
                    "place_assessments": [
                        a.model_dump(mode="json") for a in draft.planning_decision.place_assessments
                    ],
                    "quality_review": draft.planning_decision.quality_review.model_dump(mode="json"),
                    "review_outcome": draft.planning_decision.review_outcome,
                    "selected_attempt": best_attempt,
                }
            )
        return best

    def hotels(state: PlanState):
        if requirements.lodging_mode != "route_first":
            return {}
        draft = state["itinerary"]
        emit("researching_hotels", "酒店 Agent 根据每天末站和次日首站查找候选，住宿由你选择")
        with journal.task("hotel", hotel_task_input(requirements, draft, catalog)) as task:
            draft.lodging = research_hotels(requirements, draft, catalog, tools)
            task.complete(
                draft.lodging.model_dump(mode="json"),
                status=(
                    "skipped"
                    if draft.lodging.status == "not_needed"
                    else "degraded"
                    if draft.lodging.status in {"unavailable", "partial"}
                    or draft.lodging.analysis_status == "unavailable"
                    else "completed"
                ),
            )
        draft.trace = list(tools.trace)
        return {"itinerary": validate_itinerary(requirements, draft, overrides or {}, catalog)}

    graph = StateGraph(PlanState)
    graph.add_node("weather", weather)
    graph.add_node("research", research)
    graph.add_node("main", main)
    graph.add_node("hotels", hotels)
    graph.add_edge(START, "weather")
    graph.add_edge("weather", "research")
    graph.add_edge("research", "main")
    graph.add_edge("main", "hotels")
    graph.add_edge("hotels", END)
    result = graph.compile().invoke({"requirements": requirements})
    return result["itinerary"]
