from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.catalog import CATALOG
from app.config import Settings
from app.llm import model_candidates
from app.planner import build_plan
from app.schemas import Itinerary, Place, Requirements
from app.tools import TravelTools
from app.validation import validate_itinerary


class PlanState(TypedDict, total=False):
    requirements: Requirements
    order: list[str]
    itinerary: Itinerary


def generate(
    requirements: Requirements,
    settings: Settings,
    emit,
    overrides: dict | None = None,
    catalog: dict[str, Place] | None = None,
) -> Itinerary:
    catalog = catalog if catalog is not None else CATALOG
    tools = TravelTools(
        settings, requirements.city, requirements.country_code, province=requirements.province
    )

    def candidates(state: PlanState):
        emit("selecting", "整理需求与候选地点")
        if settings.planning_mode == "llm":
            return {
                "order": model_candidates(
                    state["requirements"], settings, tools.trace, emit, tools.deadline, catalog
                )
            }
        return {"order": sorted(catalog, key=lambda p: catalog[p].category not in requirements.interests)}

    def plan(state: PlanState):
        emit("planning", "查询交通并安排日程")
        itinerary = build_plan(state["requirements"], state["order"], tools, overrides or {}, catalog)
        return {"itinerary": itinerary}

    def validate(state: PlanState):
        emit("validating", "独立检查时间、必去地点与预约")
        result = validate_itinerary(state["requirements"], state["itinerary"], overrides or {}, catalog)
        return {"itinerary": result}

    graph = StateGraph(PlanState)
    graph.add_node("candidates", candidates)
    graph.add_node("plan", plan)
    graph.add_node("validate", validate)
    graph.add_edge(START, "candidates")
    graph.add_edge("candidates", "plan")
    graph.add_edge("plan", "validate")
    graph.add_edge("validate", END)
    result = graph.compile().invoke({"requirements": requirements})
    return result["itinerary"]
