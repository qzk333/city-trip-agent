from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent_contracts import GuideBrief
from app.agent_tasks import TaskJournal
from app.catalog import CATALOG
from app.config import Settings
from app.guide_agent import analyze_guides, guide_task_input
from app.hotel_agent import hotel_task_input, research_hotels
from app.llm import model_candidates
from app.planner import build_plan
from app.research import research_guides
from app.schemas import Itinerary, Place, Requirements
from app.tools import TravelTools
from app.validation import validate_itinerary


class PlanState(TypedDict, total=False):
    requirements: Requirements
    order: list[str]
    itinerary: Itinerary
    guide_brief: GuideBrief


def generate(
    requirements: Requirements,
    settings: Settings,
    emit,
    overrides: dict | None = None,
    catalog: dict[str, Place] | None = None,
    journal: TaskJournal | None = None,
) -> Itinerary:
    catalog = catalog if catalog is not None else CATALOG
    journal = journal or TaskJournal()
    tools = TravelTools(
        settings, requirements.city, requirements.country_code, province=requirements.province
    )
    recommendations: dict = {}

    def research(state: PlanState):
        task_input = guide_task_input(requirements, catalog)
        emit("researching", "攻略 Agent 独立检索与分析资料，向主 Agent 提交带来源的研究结论")
        with journal.task("guide", task_input) as task:
            evidence = research_guides(requirements, settings, tools.deadline)
            result = analyze_guides(task_input, evidence, settings, tools.deadline)
            task.complete(result.model_dump(mode="json"), status=result.task_status)
        tools.guides = result.research
        tools.trace.extend(result.trace)
        emit("researching", result.brief.summary)
        return {"guide_brief": result.brief}

    def candidates(state: PlanState):
        emit("selecting", "整理需求与候选地点")
        if settings.planning_mode == "llm":
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
            )
        else:
            order = sorted(catalog, key=lambda p: catalog[p].category not in requirements.interests)
        return {"order": order}

    def plan(state: PlanState):
        emit("planning", "查询交通并安排日程")
        tools.guides.advice = recommendations.get("transport_advice", [])
        options = dict(overrides or {})
        if recommendations.get("day_groups") and not overrides:
            options["day_groups"] = recommendations["day_groups"]
        itinerary = build_plan(state["requirements"], state["order"], tools, options, catalog)
        return {"itinerary": itinerary}

    def validate(state: PlanState):
        emit("validating", "独立检查时间、必去地点与预约")
        result = validate_itinerary(state["requirements"], state["itinerary"], overrides or {}, catalog)
        return {"itinerary": result}

    def main(state: PlanState):
        with journal.task(
            "main",
            {
                "requirements": requirements.model_dump(mode="json"),
                "guide_brief": state["guide_brief"].model_dump(mode="json"),
                "overrides": overrides or {},
            },
        ) as task:
            selected = candidates(state)
            planned = plan({**state, **selected})
            validated = validate({**state, **selected, **planned})
            draft = validated["itinerary"]
            task.complete(
                {
                    "place_ids": selected["order"],
                    "day_groups": recommendations.get("day_groups", []),
                    "itinerary_status": draft.status,
                    "days": [day.model_dump(mode="json") for day in draft.days],
                }
            )
        return {**selected, **validated}

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
    graph.add_node("research", research)
    graph.add_node("main", main)
    graph.add_node("hotels", hotels)
    graph.add_edge(START, "research")
    graph.add_edge("research", "main")
    graph.add_edge("main", "hotels")
    graph.add_edge("hotels", END)
    result = graph.compile().invoke({"requirements": requirements})
    return result["itinerary"]
