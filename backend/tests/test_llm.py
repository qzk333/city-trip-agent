import json
from copy import deepcopy

import httpx
import pytest

from app.agent import generate
from app.agent_contracts import GuideBrief, GuideFinding
from app.catalog import CATALOG
from app.config import Settings
from app.llm import MAX_TOOL_EXECUTIONS, execute_catalog_tool, model_candidates
from app.schemas import GuideResearch, GuideSource
from app.tools import ToolUnavailable


def model_settings():
    return Settings(
        planning_mode="llm",
        tool_provider="demo",
        llm_base_url="https://model.example/v1",
        llm_api_key="test-placeholder",
        llm_model="test-model",
        _env_file=None,
    )


@pytest.mark.parametrize("input_count", [100, "provider-text-must-not-persist"])
def test_model_tool_round_trip_and_structured_output(monkeypatch, requirements, input_count):
    requests = []
    responses = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_places",
                        "arguments": '{"query":"西湖"}',
                    },
                },
            ],
        },
        {"role": "assistant", "content": json.dumps({"place_ids": ["gushan_museum", "westlake", "coffee"]})},
    ]

    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": responses.pop(0)}],
                "usage": {"prompt_tokens": input_count, "completion_tokens": 30},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    result = generate(requirements, model_settings(), lambda *_: None)
    assert requests[0]["tool_choice"] == "required"
    tool_message = next(m for m in requests[1]["messages"] if m["role"] == "tool")
    assert tool_message["tool_call_id"] == "call_1"
    assert json.loads(tool_message["content"])["places"][0]["id"] == "westlake"
    assert any(t["tool"] == "search_places" for t in result.trace)
    assert result.planning_mode == "llm"
    assert all(
        t["input_tokens"] == (100 if type(input_count) is int else None)
        for t in result.trace
        if t["tool"] == "llm_candidates"
    )
    assert not [c for c in result.checks if c.severity == "error"]


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("run_shell", "{}"),
        ("search_places", '{"query":"西湖","extra":"not allowed"}'),
        ("lookup_place_notes", "not-json"),
    ],
)
def test_unknown_tools_and_invalid_arguments_are_rejected(name, arguments):
    with pytest.raises(ValueError):
        execute_catalog_tool(name, arguments)


def test_invented_model_candidates_cannot_reach_the_planner(monkeypatch, requirements):
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "lookup_place_notes", "arguments": '{"place_id":"westlake"}'},
                }
            ],
        },
        {"role": "assistant", "content": '{"place_ids":["invented-location"]}'},
    ]
    monkeypatch.setattr(
        "app.llm.httpx.post",
        lambda url, **_: httpx.Response(
            200,
            json={"choices": [{"message": messages.pop(0)}]},
            request=httpx.Request("POST", url),
        ),
    )
    with pytest.raises(ToolUnavailable, match="无效"):
        generate(requirements, model_settings(), lambda *_: None)


def test_model_cannot_loop_forever(monkeypatch, requirements):
    calls = []

    def post(url, **_):
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"call_{len(calls)}",
                                    "function": {
                                        "name": "search_places",
                                        "arguments": '{"query":""}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    with pytest.raises(ToolUnavailable, match="预算"):
        generate(requirements, model_settings(), lambda *_: None)
    assert len(calls) == 2  # Full catalog coverage immediately closes the tool phase.


def note_calls(place_ids, prefix="call"):
    return [
        {
            "id": f"{prefix}_{index}",
            "type": "function",
            "function": {"name": "lookup_place_notes", "arguments": json.dumps({"place_id": place_id})},
        }
        for index, place_id in enumerate(place_ids)
    ]


def mock_messages(monkeypatch, messages):
    requests = []
    responses = list(messages)

    def post(url, **kwargs):
        requests.append(deepcopy(kwargs["json"]))
        return httpx.Response(
            200, json={"choices": [{"message": responses.pop(0)}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    return requests


def test_normal_batch_of_four_notes_is_not_rejected(monkeypatch, requirements):
    ids = list(CATALOG)[:4]
    requests = mock_messages(
        monkeypatch,
        [
            {"role": "assistant", "tool_calls": note_calls(ids)},
            {"role": "assistant", "content": json.dumps({"place_ids": ids})},
        ],
    )
    trace = []
    assert model_candidates(requirements, model_settings(), trace, lambda *_: None) == ids
    replies = [message for message in requests[1]["messages"] if message["role"] == "tool"]
    assert len(replies) == 4
    assert all(json.loads(reply["content"])["status"] == "ok" for reply in replies)


@pytest.mark.parametrize("batch_size", [7, 10])
def test_oversized_batch_replies_to_every_call_and_finalizes_within_execution_budget(
    monkeypatch, requirements, batch_size
):
    ids = list(CATALOG)[:batch_size]
    requests = mock_messages(
        monkeypatch,
        [
            {"role": "assistant", "tool_calls": note_calls(ids)},
            {"role": "assistant", "content": json.dumps({"place_ids": ids})},
        ],
    )
    executions = []
    original = execute_catalog_tool

    def execute(*args):
        executions.append(args[0])
        return original(*args)

    monkeypatch.setattr("app.llm.execute_catalog_tool", execute)
    assert model_candidates(requirements, model_settings(), [], lambda *_: None) == ids
    assert len(executions) == MAX_TOOL_EXECUTIONS
    assert requests[1]["tool_choice"] == "none"
    replies = [message for message in requests[1]["messages"] if message["role"] == "tool"]
    assert {reply["tool_call_id"] for reply in replies} == {call["id"] for call in note_calls(ids)}
    statuses = [json.loads(reply["content"])["status"] for reply in replies]
    assert statuses.count("budget_exhausted") == batch_size - MAX_TOOL_EXECUTIONS


def test_budget_is_shared_across_batches_and_then_forces_final_json(monkeypatch, requirements):
    ids = list(CATALOG)[:8]
    requests = mock_messages(
        monkeypatch,
        [
            {"role": "assistant", "tool_calls": note_calls(ids[:4], "first")},
            {"role": "assistant", "tool_calls": note_calls(ids[4:], "second")},
            {"role": "assistant", "content": json.dumps({"place_ids": ids})},
        ],
    )
    trace = []
    assert model_candidates(requirements, model_settings(), trace, lambda *_: None) == ids
    assert requests[2]["tool_choice"] == "none"
    assert sum(entry["tool"] == "lookup_place_notes" and entry["status"] == "ok" for entry in trace) == 6
    assert sum(entry["status"] == "budget_exhausted" for entry in trace) == 2


@pytest.mark.parametrize("same_batch", [True, False])
def test_repeated_tool_queries_are_cached_and_do_not_prevent_final_output(
    monkeypatch, requirements, same_batch
):
    ids = [next(iter(CATALOG))]
    first = note_calls(ids, "first")
    repeated = note_calls(ids * 7, "repeat")
    # Whitespace differences still address the same validated query.
    repeated[0]["function"]["arguments"] = json.dumps({"place_id": ids[0]}, indent=2)
    messages = [{"role": "assistant", "tool_calls": first + repeated if same_batch else first}]
    if not same_batch:
        messages.append({"role": "assistant", "tool_calls": repeated})
    messages.append({"role": "assistant", "content": json.dumps({"place_ids": ids})})
    requests = mock_messages(monkeypatch, messages)
    trace = []
    assert model_candidates(requirements, model_settings(), trace, lambda *_: None) == ids
    assert sum(entry["tool"] == "lookup_place_notes" and entry["status"] == "ok" for entry in trace) == 1
    assert sum(entry["status"] == "cache_hit" for entry in trace) == 7
    if not same_batch:
        assert requests[-1]["tool_choice"] == "none"


def test_invalid_tool_in_batch_is_rejected_before_any_execution(monkeypatch, requirements):
    calls = note_calls(list(CATALOG))
    calls[-1]["function"]["name"] = "run_shell"
    mock_messages(monkeypatch, [{"role": "assistant", "tool_calls": calls}])
    executions = []
    monkeypatch.setattr("app.llm.execute_catalog_tool", lambda *args: executions.append(args))
    with pytest.raises(ToolUnavailable, match="协议兼容性"):
        model_candidates(requirements, model_settings(), [], lambda *_: None)
    assert executions == []


def test_model_day_groups_and_transport_advice_only_accept_retrieved_sources(monkeypatch, requirements):
    ids = list(CATALOG)[:3]
    source = GuideSource(
        id="guide-1", title="杭州交通攻略", url="https://guide.example/travel", excerpt="古城建议步行。"
    )
    content = {
        "place_ids": ids,
        "day_groups": [ids],
        "transport_advice": [
            {
                "place_ids": ids,
                "mode": "walking",
                "advice": "古城相邻景点建议步行。",
                "source_ids": ["guide-1"],
            },
            {
                "place_ids": ids,
                "mode": "driving",
                "advice": "不存在的攻略建议。",
                "source_ids": ["invented-source"],
            },
        ],
    }
    requests = mock_messages(
        monkeypatch,
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "search", "function": {"name": "search_places", "arguments": '{"query":""}'}}
                ],
            },
            {"role": "assistant", "content": json.dumps(content)},
        ],
    )
    recommendations = {}
    result = model_candidates(
        requirements,
        model_settings(),
        [],
        lambda *_: None,
        research=GuideResearch(status="ok", sources=[source]),
        recommendations=recommendations,
        guide_brief=GuideBrief(
            status="ready",
            summary="相邻景点可考虑步行。",
            findings=[GuideFinding(claim="相邻景点可考虑步行。", source_ids=["guide-1"])],
        ),
    )
    assert result == ids and recommendations["day_groups"] == [ids]
    assert len(recommendations["transport_advice"]) == 1
    assert recommendations["transport_advice"][0].source_ids == ["guide-1"]
    handoff = json.loads(requests[0]["messages"][1]["content"])
    assert handoff["guide_sources"][0]["id"] == "guide-1"
    assert "古城建议步行。" not in json.dumps(handoff, ensure_ascii=False)


@pytest.mark.parametrize(
    "base_url,deepseek",
    [
        ("https://api.deepseek.com", True),
        ("https://api.deepseek.com/v1", True),
        ("https://api.deepseek.com.example/v1", False),
        ("https://model.example/v1", False),
    ],
)
def test_official_deepseek_uses_non_thinking_mode_for_required_tool_calls(
    monkeypatch, requirements, base_url, deepseek
):
    requests = []
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_places", "arguments": '{"query":""}'},
                }
            ],
        },
        {"role": "assistant", "content": '{"place_ids":["westlake"]}'},
    ]

    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(
            200, json={"choices": [{"message": messages.pop(0)}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    settings = model_settings().model_copy(update={"llm_base_url": base_url})
    result = model_candidates(requirements, settings, [], lambda *_: None)
    assert result == ["westlake"]
    assert requests[0]["tool_choice"] == "required"
    assert all(
        request.get("thinking") == ({"type": "disabled"} if deepseek else None) for request in requests
    )


@pytest.mark.parametrize(
    "status,fragment", [(400, "请求参数被拒绝"), (401, "认证"), (402, "余额"), (404, "不存在"), (429, "限流")]
)
def test_planning_http_errors_are_actionable_without_echoing_provider_payload(
    monkeypatch, requirements, status, fragment
):
    def post(url, **kwargs):
        return httpx.Response(
            status, json={"error": {"message": "secret-key-echo"}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.llm.httpx.post", post)
    with pytest.raises(ToolUnavailable) as failure:
        model_candidates(requirements, model_settings(), [], lambda *_: None)
    assert fragment in str(failure.value)
    assert f"HTTP {status}" in str(failure.value)
    assert "secret-key-echo" not in str(failure.value)


@pytest.mark.parametrize(
    "choice,fragment",
    [
        ({"finish_reason": "length", "message": {"content": "truncated"}}, "截断"),
        ({"message": {"role": "assistant", "content": '{"place_ids":["westlake"]}'}}, "未执行"),
        (None, "结构无效"),
    ],
)
def test_planning_protocol_failure_identifies_the_failed_stage(monkeypatch, requirements, choice, fragment):
    monkeypatch.setattr(
        "app.llm.httpx.post",
        lambda url, **kwargs: httpx.Response(
            200, json={"choices": [choice]}, request=httpx.Request("POST", url)
        ),
    )
    with pytest.raises(ToolUnavailable, match=fragment):
        model_candidates(requirements, model_settings(), [], lambda *_: None)
