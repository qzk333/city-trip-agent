from time import monotonic

from test_candidate_quality import poi
from test_model_planning_decisions import runtime
from test_timed_weather_planning import day, forecast, stop

from app.agent import generate
from app.agent_contracts import GuideBrief, GuideFinding, GuidePlaceSuggestion, GuideTaskResult
from app.database import CatalogRecord
from app.guide_discovery import admit_guide_candidates, expand_guide_candidates
from app.schemas import GuideResearch, GuideSource, Requirements, TravelProfile
from app.tools import ToolUnavailable, TravelTools


def report(names=("南头古城",), excerpt=None):
    return GuideTaskResult(
        research=GuideResearch(
            status="partial",
            fetched_at="2026-10-26T12:00:00Z",
            sources=[
                GuideSource(
                    id="guide-1",
                    title="深圳游玩建议",
                    url="https://guide.example/shenzhen",
                    excerpt=excerpt or "可比较" + "、".join(names),
                )
            ],
        ),
        brief=GuideBrief(
            status="ready",
            summary="城市文化体验与夜游相结合",
            findings=[GuideFinding(claim="可比较城市文化体验", source_ids=["guide-1"])],
            place_suggestions=[
                GuidePlaceSuggestion(
                    name=name, category="city", reason="补充攻略中的文化区域", source_ids=["guide-1"]
                )
                for name in names
            ],
        ),
        trace=[],
    )


def amap_tools():
    return TravelTools(
        runtime().model_copy(update={"tool_provider": "amap", "amap_api_key": "fixture"}),
        city="深圳",
        adcode="440300",
    )


def req(**kwargs):
    return Requirements(start_date="2026-10-27", lodging_mode="route_first", **kwargs)


def test_sourced_new_name_is_resolved_and_original_catalog_is_unchanged(monkeypatch):
    original = poi("original", "城市博物馆")
    new = poi("nantou", "南头古城", "city")
    tools = amap_tools()
    calls = []

    def search(name, category, **kwargs):
        calls.append((name, kwargs["limit"]))
        return {new.id: new}

    monkeypatch.setattr(tools, "search_candidates", search)
    catalog = {original.id: original}
    expanded, result = expand_guide_candidates(req(), report(), tools, catalog)
    assert calls == [("南头古城", 3)]
    assert new.id in expanded and new.id not in catalog
    assert result.brief.place_resolutions[0].place_ids == [new.id]
    assert result.research.discovered_place_ids == [new.id]
    assert {e.field for e in expanded[new.id].traits.evidence} >= {"identity", "retrieval", "guide_reference"}
    assert req().must_visit == []


def test_unbacked_or_excluded_names_do_not_consume_map_queries(monkeypatch):
    tools = amap_tools()
    monkeypatch.setattr(
        tools,
        "search_candidates",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected query")),
    )
    _, result = expand_guide_candidates(req(), report(excerpt="只提到莲花山"), tools, {})
    assert result.brief.place_resolutions[0].status == "unsupported"
    _, result = expand_guide_candidates(
        req(profile=TravelProfile(excluded_keywords=["古城"])), report(), tools, {}
    )
    assert result.brief.place_resolutions[0].status == "excluded"


def test_duplicate_existing_place_is_reused_without_map_request(monkeypatch):
    tools = amap_tools()
    p = poi("nantou", "南头古城", "city")
    monkeypatch.setattr(
        tools,
        "search_candidates",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected query")),
    )
    expanded, result = expand_guide_candidates(req(), report(("南头古城", "南头古城")), tools, {p.id: p})
    assert len(expanded) == 1 and result.research.discovered_place_ids == []
    assert len(result.brief.place_resolutions) == 1 and result.brief.place_resolutions[0].status == "existing"


def test_ambiguous_names_are_not_admitted(monkeypatch):
    tools = amap_tools()
    a = poi("a", "南头古城", "city")
    b = poi("b", "南头古城", "city")
    monkeypatch.setattr(tools, "search_candidates", lambda *_a, **_k: {a.id: a, b.id: b})
    expanded, result = expand_guide_candidates(req(), report(), tools, {})
    assert expanded == {} and result.brief.place_resolutions[0].status == "ambiguous"
    assert result.brief.place_resolutions[0].place_ids == []


def test_cross_city_and_ticket_counter_results_cannot_enter_guide_pool(monkeypatch):
    tools = amap_tools()
    rows = [
        {
            "id": "wrong-city",
            "name": "南头古城",
            "location": "113.1,23.1",
            "typecode": "110000",
            "cityname": "广州市",
            "adcode": "440100",
        },
        {
            "id": "counter",
            "name": "南头古城售票处",
            "location": "113.9,22.5",
            "typecode": "110000",
            "cityname": "深圳市",
            "adcode": "440300",
        },
    ]
    monkeypatch.setattr(tools, "_get", lambda *_: {"pois": rows})
    expanded, result = expand_guide_candidates(req(), report(), tools, {})
    assert expanded == {} and result.brief.place_resolutions[0].status == "no_result"


def test_guide_model_cannot_invent_source_for_new_place(monkeypatch):
    from test_agent_handoffs import analysis, evidence, model_settings, reply

    from app.catalog import CATALOG
    from app.guide_agent import analyze_guides, guide_task_input

    payload = analysis()
    payload["place_suggestions"] = [
        {"name": "南头古城", "reason": "建议比较", "source_ids": ["invented-source"]}
    ]
    monkeypatch.setattr("app.guide_agent.httpx.post", lambda url, **_: reply(url, payload))
    result = analyze_guides(guide_task_input(req(), CATALOG), evidence(), model_settings(), monotonic() + 60)
    assert result.brief.status == "unavailable" and result.brief.place_suggestions == []


def test_guide_discovery_has_shared_budget_and_preserves_failure_detail_safely(monkeypatch):
    tools = amap_tools()
    calls = []

    def search(*args, **kwargs):
        calls.append(1)
        raise ToolUnavailable("contains-private-key")

    monkeypatch.setattr(tools, "search_candidates", search)
    _, result = expand_guide_candidates(req(), report(tuple(f"景点{i}" for i in range(6))), tools, {})
    assert len(calls) == 3
    assert [r.status for r in result.brief.place_resolutions] == ["unavailable"] * 3 + ["skipped"] * 3
    assert "private-key" not in result.model_dump_json()
    tools.deadline = monotonic() + 10
    _, result = expand_guide_candidates(req(), report(), tools, {})
    assert len(calls) == 3 and result.brief.place_resolutions[0].status == "skipped"


def test_expanded_guide_place_survives_main_candidate_limit_without_becoming_mandatory(monkeypatch):
    tools = amap_tools()
    new = poi("new", "南头古城", "city")
    monkeypatch.setattr(tools, "search_candidates", lambda *_a, **_k: {new.id: new})
    catalog = {p.id: p for p in [poi(str(i), f"历史博物馆{i}") for i in range(50)]}
    expanded, result = expand_guide_candidates(req(), report(), tools, catalog)
    requirements = req(must_visit=["amap-49"])
    admitted = admit_guide_candidates(requirements, expanded, result.brief)
    assert len(admitted) == 40 and new.id in admitted and "amap-49" in admitted
    assert new.id not in requirements.must_visit


def setup_pipeline(monkeypatch):
    old = poi("old", "城市博物馆")
    new = poi("new", "南头古城", "city")
    settings = runtime().model_copy(update={"tool_provider": "amap", "amap_api_key": "fixture"})
    monkeypatch.setattr("app.agent.research_guides", lambda *_: report().research)
    monkeypatch.setattr("app.agent.analyze_guides", lambda *_: report())
    monkeypatch.setattr(TravelTools, "weather", lambda *_: forecast())
    monkeypatch.setattr(TravelTools, "search_candidates", lambda *_a, **_k: {new.id: new})
    seen = []

    def select(requirements, settings, trace, emit, deadline, catalog, guides, output, **kwargs):
        assert new.id in catalog
        seen.append(kwargs["guide_brief"].place_resolutions[0].place_ids)
        output["daily_schedule"] = [day([stop(new.id, "10:00", "11:00")])]
        output["day_groups"] = [[new.id]]
        return [new.id]

    monkeypatch.setattr("app.agent.model_candidates", select)
    return old, new, settings, seen


def test_main_receives_expanded_catalog_and_resolved_report(monkeypatch):
    old, new, settings, seen = setup_pipeline(monkeypatch)
    updated = {}
    result = generate(req(), settings, lambda *_: None, catalog={old.id: old}, catalog_updates=updated)
    assert seen == [[new.id]] and new.id in updated
    assert result.days[0].activities[0].place_id == new.id
    assert result.guides.discovered_place_ids == [new.id]


def test_expansion_is_atomic_with_preview_confirmation_and_reusable_catalog(client, monkeypatch):
    old, new, settings, _ = setup_pipeline(monkeypatch)
    monkeypatch.setattr("app.main.request_settings", lambda *_: settings)
    with client.app.state.session_factory.begin() as db:
        snapshot = CatalogRecord(city_id=19598, places={old.id: old.model_dump()})
        db.add(snapshot)
        db.flush()
        original_id = snapshot.id
    created = client.post(
        "/api/trips",
        json={"start_date": "2026-10-27", "catalog_id": original_id, "lodging_mode": "route_first"},
    ).json()
    run_id = client.post(f"/api/trips/{created['id']}/plan-runs").json()["run_id"]
    proposal = client.get(f"/api/runs/{run_id}").json()["proposal"]
    assert new.id in {p["id"] for p in proposal["places"]}
    assert proposal["requirements"]["catalog_id"] != original_id
    before = client.get(f"/api/trips/{created['id']}").json()
    assert before["current_version"] == 0 and new.id not in {p["id"] for p in before["places"]}
    response = client.post(
        f"/api/trips/{created['id']}/proposals/{proposal['id']}/apply",
        json={"base_version": 0, "idempotency_key": "guide-confirm"},
    )
    assert response.status_code == 200, response.text
    saved = client.get(f"/api/trips/{created['id']}").json()
    assert new.id in {p["id"] for p in saved["places"]}
    reused = client.post("/api/trips", json={**saved["requirements"], "must_visit": [new.id]})
    assert reused.status_code == 201, reused.text
