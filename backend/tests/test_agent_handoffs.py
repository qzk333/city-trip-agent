import json
from copy import deepcopy
from time import monotonic

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import generate
from app.agent_contracts import GuideBrief, GuideTaskResult
from app.catalog import CATALOG
from app.config import Settings
from app.database import AgentTaskRecord, RunRecord, TripRecord
from app.guide_agent import analyze_guides, guide_task_input
from app.llm import model_candidates
from app.main import create_app
from app.schemas import GuideResearch, GuideSource
from app.tools import ToolUnavailable


def model_settings(**kwargs):
    return Settings(
        planning_mode="llm",
        llm_base_url="https://model.example/v1",
        llm_api_key="test-placeholder-key",
        llm_model="test-model",
        _env_file=None,
        **kwargs,
    )


def evidence():
    return GuideResearch(
        status="partial",
        sources=[
            GuideSource(
                id="guide-1",
                title="杭州游玩建议",
                url="https://guide.example/hangzhou",
                excerpt="UNTRUSTED_PAGE_SENTINEL 西湖周边可以步行，班次需要核实。",
            )
        ],
    )


def analysis():
    return {
        "summary": "西湖周边适合集中安排，交通条件需要确认。",
        "findings": [{"claim": "西湖周边可考虑步行。", "source_ids": ["guide-1"], "place_ids": ["westlake"]}],
        "unknowns": ["具体开放时间未核实。"],
    }


def reply(url, content, **kwargs):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}]},
        request=httpx.Request("POST", url),
    )


def test_guide_has_private_context_and_main_only_receives_validated_handoff(monkeypatch, requirements):
    requests = []
    monkeypatch.setattr("app.agent.research_guides", lambda *_: evidence())

    def post(url, **kwargs):
        body = kwargs["json"]
        requests.append(deepcopy(body))
        if "tools" not in body:
            return reply(url, analysis())
        if not any(m["role"] == "tool" for m in body["messages"]):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "catalog",
                                        "function": {"name": "search_places", "arguments": '{"query":""}'},
                                    }
                                ],
                            }
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )
        return reply(url, {"place_ids": list(CATALOG)})

    monkeypatch.setattr("httpx.post", post)
    result = generate(requirements, model_settings(), lambda *_: None)
    assert len(requests) == 3  # One bounded research request, then the existing planner tool loop.
    assert "UNTRUSTED_PAGE_SENTINEL" in json.dumps(requests[0])
    assert all("UNTRUSTED_PAGE_SENTINEL" not in json.dumps(body) for body in requests[1:])
    handoff = json.loads(requests[1]["messages"][1]["content"])
    assert handoff["guide_brief"]["findings"][0]["source_ids"] == ["guide-1"]
    assert "evidence" not in handoff and "guide_research" not in handoff
    assert result.guides.sources[0].excerpt == evidence().sources[0].excerpt
    assert any(t["tool"] == "guide_agent" and t["status"] == "ok" for t in result.trace)


@pytest.mark.parametrize("bad_field", ["source_ids", "place_ids"])
def test_guide_rejects_invented_references_without_forwarding_claim(monkeypatch, requirements, bad_field):
    invalid = analysis()
    invalid["findings"][0][bad_field] = ["invented-reference"]
    monkeypatch.setattr("httpx.post", lambda url, **_: reply(url, invalid))
    result = analyze_guides(
        guide_task_input(requirements, CATALOG), evidence(), model_settings(), monotonic() + 60
    )
    assert result.task_status == "degraded"
    assert result.brief.status == "unavailable" and result.brief.findings == []
    assert invalid["findings"][0]["claim"] not in result.brief.model_dump_json()
    assert len(result.research.sources) == 1  # Rejected model output does not discard retrieved evidence.


def test_guide_failure_is_bounded_sanitized_and_does_not_abort_main(monkeypatch, requirements):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(
            429, json={"error": "secret-upstream-payload"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("httpx.post", post)
    result = analyze_guides(
        guide_task_input(requirements, CATALOG), evidence(), model_settings(), monotonic() + 60
    )
    assert len(calls) == 1 and calls[0]["timeout"] <= 15
    assert result.task_status == "degraded" and result.brief.findings == []
    assert "secret-upstream-payload" not in result.model_dump_json()
    assert "test-placeholder-key" not in result.model_dump_json()


def test_guide_reserves_planning_time_without_another_model_call(monkeypatch, requirements):
    def unexpected(*_, **__):
        pytest.fail("Research must not consume the reserved planning budget")

    monkeypatch.setattr("httpx.post", unexpected)
    result = analyze_guides(
        guide_task_input(requirements, CATALOG), evidence(), model_settings(), monotonic() + 10
    )
    assert result.task_status == "degraded" and result.trace[-1]["status"] == "budget_exhausted"


def test_main_cannot_claim_sources_after_failed_guide_analysis(monkeypatch, requirements):
    messages = [
        {"tool_calls": [{"id": "all", "function": {"name": "search_places", "arguments": '{"query":""}'}}]},
        {
            "content": json.dumps(
                {
                    "place_ids": list(CATALOG),
                    "transport_advice": [
                        {
                            "place_ids": ["westlake"],
                            "mode": "walking",
                            "advice": "未通过分析的建议",
                            "source_ids": ["guide-1"],
                        }
                    ],
                }
            )
        },
    ]
    monkeypatch.setattr(
        "httpx.post",
        lambda url, **_: httpx.Response(
            200, json={"choices": [{"message": messages.pop(0)}]}, request=httpx.Request("POST", url)
        ),
    )
    recommendations = {}
    model_candidates(
        requirements,
        model_settings(),
        [],
        lambda *_: None,
        research=evidence(),
        recommendations=recommendations,
        guide_brief=GuideBrief(status="unavailable", summary="分析未通过。"),
    )
    assert recommendations["transport_advice"] == []


def test_completed_research_survives_planner_failure_and_application_restart(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'agent.sqlite3').as_posix()}",
        local_api_config_path=tmp_path / "keys.json",
        _env_file=None,
    )
    monkeypatch.setattr("app.agent.research_guides", lambda *_: evidence())
    monkeypatch.setattr(
        "app.agent.analyze_guides",
        lambda _, research, *__: GuideTaskResult(
            research=research, brief=GuideBrief(status="ready", **analysis())
        ),
    )

    def fail(*_, **__):
        raise ToolUnavailable("路线查询暂时不可用")

    monkeypatch.setattr("app.agent.build_plan", fail)
    with TestClient(create_app(settings)) as client:
        trip = client.post("/api/trips", json={"start_date": "2026-10-24"}).json()
        run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "failed" and run["proposal"] is None
        tasks = client.get(f"/api/runs/{run_id}/agent-tasks").json()
        assert [(t["role"], t["status"]) for t in tasks] == [("guide", "completed"), ("main", "failed")]
        assert tasks[0]["result"]["research"]["sources"][0]["id"] == "guide-1"
        assert "UNTRUSTED_PAGE_SENTINEL" not in json.dumps(tasks[1])
        assert all(t["finished_at"] for t in tasks)
        assert "result" not in run["agent_tasks"][0]  # Normal polling doesn't copy all evidence.
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/runs/{run_id}/agent-tasks").json() == tasks
        assert client.get(f"/api/trips/{trip['id']}").json()["current_version"] == 0
        assert client.get("/api/runs/missing/agent-tasks").status_code == 404


def test_demo_run_records_tasks_without_calling_research_model(client, monkeypatch):
    def unexpected(*_, **__):
        pytest.fail("Demo mode must not make model calls")

    monkeypatch.setattr("httpx.post", unexpected)
    trip = client.post("/api/trips", json={"start_date": "2026-10-24"}).json()
    run_id = client.post(f"/api/trips/{trip['id']}/plan-runs").json()["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed"
    tasks = client.get(f"/api/runs/{run_id}/agent-tasks").json()
    assert [(t["role"], t["status"]) for t in tasks] == [("guide", "skipped"), ("main", "completed")]
    assert tasks[1]["result"]["days"] == run["proposal"]["itinerary"]["days"]


def test_startup_marks_unfinished_tasks_interrupted_but_keeps_completed_outputs(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'restart.sqlite3').as_posix()}",
        local_api_config_path=tmp_path / "keys.json",
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app):
        with app.state.session_factory.begin() as db:
            trip = TripRecord(requirements={}, catalog={})
            db.add(trip)
            db.flush()
            run = RunRecord(trip_id=trip.id, base_version=0, status="running")
            db.add(run)
            db.flush()
            run_id = run.id
            db.add_all(
                [
                    AgentTaskRecord(
                        run_id=run_id,
                        role="guide",
                        status="completed",
                        input_snapshot={},
                        result={"evidence_ref": "kept"},
                        finished_at="2026-10-23T00:00:00Z",
                    ),
                    AgentTaskRecord(run_id=run_id, role="main", status="running", input_snapshot={}),
                ]
            )
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "failed"
        tasks = {t["role"]: t for t in client.get(f"/api/runs/{run_id}/agent-tasks").json()}
        assert tasks["main"]["status"] == "interrupted" and tasks["main"]["finished_at"]
        assert tasks["guide"]["status"] == "completed"
        assert tasks["guide"]["result"] == {"evidence_ref": "kept"}
