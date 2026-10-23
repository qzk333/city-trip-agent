import json

import httpx
import pytest

from app.agent import generate
from app.config import Settings
from app.llm import execute_catalog_tool
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
    assert len(calls) == 4
