import json

import httpx
import pytest
from test_candidate_quality import poi

from app.agent import generate
from app.candidate_discovery import merge_query, query_plan
from app.config import Settings
from app.llm import model_candidates
from app.schemas import PlaceRecommendation, Requirements, TravelProfile
from app.tools import ToolUnavailable


def runtime():
    return Settings(
        planning_mode="llm",
        tool_provider="demo",
        llm_base_url="https://model.example/v1",
        llm_model="test-model",
        llm_api_key="fixture",
        _env_file=None,
    )


def model_reply(monkeypatch, content):
    messages = [
        {
            "tool_calls": [
                {"id": "catalog", "function": {"name": "search_places", "arguments": '{"query":""}'}}
            ]
        },
        {"content": json.dumps(content)},
    ]
    monkeypatch.setattr(
        "app.llm.httpx.post",
        lambda url, **_: httpx.Response(
            200, json={"choices": [{"message": messages.pop(0)}]}, request=httpx.Request("POST", url)
        ),
    )


def test_city_overview_is_not_removed_by_narrow_interests():
    queries = query_plan(TravelProfile(themes=["history", "architecture", "night"]))
    assert queries[0] == ("景点", "nature") and len(queries) <= 6


def test_retrieval_evidence_accumulates_without_claiming_popularity():
    place = poi("island", "城市江心洲", "nature")
    pool = {}
    merge_query(pool, {place.id: place}, "景点", "测试城市")
    merge_query(pool, {place.id: place}, "公园", "测试城市")
    evidence = pool[place.id].traits.evidence
    assert {e.value for e in evidence if e.field == "retrieval"} == {"景点", "公园"}
    assert all("不是热度排名" in e.excerpt for e in evidence if e.field == "retrieval")
    assert any(e.field == "identity" for e in evidence)


def test_model_day_selection_and_landmark_survive_program_spatial_pass(monkeypatch):
    places = [
        poi("a", "同区博物馆", lon=120.16),
        poi("b", "同区街区", "city", lon=120.1601),
        poi("z", "橘子洲风景名胜区", "nature", lon=120.2),
    ]
    catalog = {p.id: p for p in places}
    groups = [["amap-z"], ["amap-a", "amap-b"]]

    def select(req, settings, trace, emit, deadline, visible, guides, recommendations, **_):
        recommendations["day_groups"] = groups
        recommendations["place_reasons"] = [
            PlaceRecommendation(
                place_id="amap-z",
                reason="以城市江心洲作为自然景观主题，与次日文化体验形成区别。",
                role="landmark",
                tradeoff="代表性为模型判断，开放与内部步行仍需核实。",
            )
        ]
        trace.append({"tool": "llm_candidates", "status": "ok"})
        return ["amap-a", "amap-b", "amap-z"]

    monkeypatch.setattr("app.agent.model_candidates", select)
    monkeypatch.setattr("app.agent.group_candidates", lambda *_: pytest.fail("must not overwrite model days"))
    req = Requirements(start_date="2026-10-26", days=2, lodging_mode="fixed")
    result = generate(req, runtime(), lambda *_: None, catalog=catalog)
    assert [[a.place_id for a in d.activities] for d in result.days] == groups
    assert result.planning_decision.source == "model_days"
    assert result.planning_decision.model_calls == 1
    assert result.planning_decision.recommendations[0].role == "landmark"
    assert not [c for c in result.checks if c.severity == "error"]


def test_one_day_model_can_see_landmark_outside_rule_shortlist(monkeypatch):
    catalog = {
        p.id: p
        for p in [*(poi(str(i), f"历史馆{i}") for i in range(12)), poi("zz", "城市代表江心洲", "nature")]
    }

    def select(req, settings, trace, emit, deadline, visible, guides, recommendations, **_):
        assert "amap-zz" in visible and len(visible) == 13
        recommendations["day_groups"] = [["amap-zz"]]
        return ["amap-zz"]

    monkeypatch.setattr("app.agent.model_candidates", select)
    result = generate(
        Requirements(start_date="2026-10-26", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
        catalog=catalog,
    )
    assert [a.place_id for a in result.days[0].activities] == ["amap-zz"]


@pytest.mark.parametrize("groups", [[["amap-outsider"], []], [["amap-a"]]])
def test_invalid_model_days_are_not_silently_replaced(monkeypatch, groups):
    p = poi("a", "历史馆")
    model_reply(monkeypatch, {"place_ids": [p.id], "day_groups": groups})
    with pytest.raises(ToolUnavailable, match="分天"):
        model_candidates(
            Requirements(start_date="2026-10-26", days=2),
            runtime(),
            [],
            lambda *_: None,
            catalog={p.id: p},
            recommendations={},
        )


def test_reason_ids_and_citations_are_validated(monkeypatch):
    p = poi("a", "历史馆")
    model_reply(
        monkeypatch,
        {
            "place_ids": [p.id],
            "day_groups": [[p.id]],
            "reasons": [
                {"place_id": p.id, "reason": "历史主题", "role": "interest"},
                {"place_id": "invented", "reason": "伪造地点"},
                {"place_id": p.id, "reason": "假引用", "source_ids": ["invented-source"]},
            ],
        },
    )
    output = {}
    model_candidates(
        Requirements(start_date="2026-10-26"),
        runtime(),
        [],
        lambda *_: None,
        catalog={p.id: p},
        recommendations=output,
    )
    assert [r.reason for r in output["place_reasons"]] == ["历史主题"]


def test_unfittable_model_stop_blocks_confirmation_instead_of_disappearing(monkeypatch):
    p = poi("a", "历史馆")

    def select(req, settings, trace, emit, deadline, visible, guides, recommendations, **_):
        recommendations["day_groups"] = [[p.id]]
        return [p.id]

    monkeypatch.setattr("app.agent.model_candidates", select)
    result = generate(
        Requirements(start_date="2026-10-26", day_end="09:10", lodging_mode="route_first"),
        runtime(),
        lambda *_: None,
        catalog={p.id: p},
    )
    assert result.status == "NO_SOLUTION_WITHIN_LIMIT"
    assert any(c.code == "model_plan_incomplete" for c in result.checks)


def test_legacy_model_order_has_explicit_program_grouping_label(monkeypatch):
    p = poi("a", "历史馆")
    monkeypatch.setattr("app.agent.model_candidates", lambda *args, **kwargs: [p.id])
    result = generate(Requirements(start_date="2026-10-26"), runtime(), lambda *_: None, catalog={p.id: p})
    assert result.planning_decision.source == "model_order"
    assert "程序完成" in result.planning_decision.notes[0]
    assert result.planning_decision.recommendations[0].basis == "rule"
