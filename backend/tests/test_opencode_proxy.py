"""Regression tests for the opencode Go Responses proxy adapters.

The Chat Completions surface has to be translated back into Responses events.
An earlier version only handled ``reasoning_content`` and ``content`` deltas, so
every tool-using turn reached Codex as a successful but *empty* answer.
"""

from __future__ import annotations

import json

from backend.services import opencode_proxy


class _FakeStream:
    """Minimal stand-in for a streaming ``requests.Response``."""

    def __init__(self, chunks):
        self._lines: list[str] = []
        for chunk in chunks:
            self._lines.append("data: " + json.dumps(chunk, ensure_ascii=False))
            self._lines.append("")
        self._lines.append("data: [DONE]")
        self._lines.append("")

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines


def _collect(events) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for raw in events:
        for block in raw.split("\n\n"):
            lines = [line for line in block.split("\n") if line]
            if len(lines) != 2:
                continue
            parsed.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return parsed


def _events_of(events: list[tuple[str, dict]], name: str) -> list[dict]:
    return [data for event, data in events if event == name]


_TOOL_CALL_CHUNKS = [
    {"choices": [{"index": 0, "delta": {"reasoning_content": "Let me search. "}}]},
    {"choices": [{"index": 0, "delta": {"reasoning_content": "Using web_search."}}]},
    {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": ""},
                        }
                    ]
                },
            }
        ]
    },
    {
        "choices": [
            {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"query": "arena'}}]}}
        ]
    },
    {
        "choices": [
            {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '.ai"}'}}]}}
        ]
    },
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    {
        "choices": [],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 64},
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    },
]


def test_stream_chat_maps_tool_calls_into_function_call_items():
    events = _collect(opencode_proxy._stream_chat_as_responses(_FakeStream(_TOOL_CALL_CHUNKS), "deepseek-flash"))

    added = _events_of(events, "response.output_item.added")
    function_calls = [item["item"] for item in added if item["item"]["type"] == "function_call"]
    assert len(function_calls) == 1
    assert function_calls[0]["call_id"] == "call_abc"
    assert function_calls[0]["name"] == "web_search"

    deltas = _events_of(events, "response.function_call_arguments.delta")
    assert "".join(data["delta"] for data in deltas) == '{"query": "arena.ai"}'

    done = _events_of(events, "response.output_item.done")
    finished_call = [item["item"] for item in done if item["item"]["type"] == "function_call"]
    assert finished_call[0]["arguments"] == '{"query": "arena.ai"}'
    assert finished_call[0]["status"] == "completed"

    completed = _events_of(events, "response.completed")[0]["response"]
    kinds = [item["type"] for item in completed["output"]]
    assert kinds == ["reasoning", "function_call"]
    assert completed["usage"]["input_tokens"] == 100
    assert completed["usage"]["input_tokens_details"]["cached_tokens"] == 64
    assert completed["usage"]["output_tokens_details"]["reasoning_tokens"] == 12


def test_stream_chat_maps_text_and_reasoning_together():
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "think"}}]},
        {"choices": [{"index": 0, "delta": {"content": "OK"}}]},
        {"choices": [{"index": 0, "delta": {"content": "!"}}]},
    ]

    events = _collect(opencode_proxy._stream_chat_as_responses(_FakeStream(chunks), "deepseek-flash"))

    added = _events_of(events, "response.output_item.added")
    assert [item["output_index"] for item in added] == [0, 1]
    assert added[0]["item"]["type"] == "reasoning"
    assert added[1]["item"]["type"] == "message"
    assert "".join(data["delta"] for data in _events_of(events, "response.output_text.delta")) == "OK!"

    done = _events_of(events, "response.output_item.done")
    assert done[0]["item"]["summary"] == [{"type": "summary_text", "text": "think"}]
    assert done[1]["item"]["content"] == [{"type": "output_text", "text": "OK!", "annotations": []}]
    assert _events_of(events, "response.completed")[0]["response"]["output"][1]["content"] == done[1]["item"]["content"]


def test_responses_to_chat_asks_for_usage_only_when_streaming():
    body = {"model": "deepseek-flash", "input": "hi"}

    assert "stream_options" not in opencode_proxy.responses_to_chat(body)
    assert opencode_proxy.responses_to_chat(body, stream=True)["stream_options"] == {"include_usage": True}


_PARALLEL_TOOL_INPUT = [
    {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Checking two things."}],
    },
    {
        "type": "function_call",
        "call_id": "call_a",
        "name": "search",
        "arguments": '{"q": "a"}',
    },
    {
        "type": "function_call",
        "call_id": "call_b",
        "name": "fetch",
        "arguments": '{"url": "b"}',
    },
    {"type": "function_call_output", "call_id": "call_a", "output": "A"},
    {"type": "function_call_output", "call_id": "call_b", "output": "B"},
]


def test_responses_to_chat_groups_parallel_tool_calls_into_one_message():
    payload = opencode_proxy.responses_to_chat({"model": "deepseek-flash", "input": _PARALLEL_TOOL_INPUT})
    messages = payload["messages"]

    assert [message["role"] for message in messages] == ["assistant", "tool", "tool"]
    assistant = messages[0]
    assert assistant["content"] == "Checking two things."
    assert [call["id"] for call in assistant["tool_calls"]] == ["call_a", "call_b"]
    assert messages[1] == {"role": "tool", "tool_call_id": "call_a", "content": "A"}
    assert messages[2] == {"role": "tool", "tool_call_id": "call_b", "content": "B"}


def test_responses_to_chat_passes_reasoning_content_back_to_deepseek():
    body = {
        "model": "deepseek-flash",
        "input": [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Let me check. "}],
                "content": [{"type": "reasoning_text", "text": "Using search."}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Checking."}],
            },
            {"type": "function_call", "call_id": "call_a", "name": "search", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_a", "output": "A"},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next"}]},
        ],
    }

    messages = opencode_proxy.responses_to_chat(body)["messages"]

    assert [message["role"] for message in messages] == ["assistant", "tool", "user"]
    assistant = messages[0]
    assert assistant["reasoning_content"] == "Let me check. Using search."
    assert assistant["content"] == "Checking."
    assert assistant["tool_calls"][0]["id"] == "call_a"


def test_responses_to_messages_groups_parallel_tool_calls_into_one_message():
    payload = opencode_proxy.responses_to_messages({"model": "claude-x", "input": _PARALLEL_TOOL_INPUT})
    messages = payload["messages"]

    assert [message["role"] for message in messages] == ["assistant", "user"]
    assistant_blocks = messages[0]["content"]
    assert assistant_blocks[0] == {"type": "text", "text": "Checking two things."}
    tool_uses = [block for block in assistant_blocks if block["type"] == "tool_use"]
    assert [block["id"] for block in tool_uses] == ["call_a", "call_b"]
    results = messages[1]["content"]
    assert [block["type"] for block in results] == ["tool_result", "tool_result"]
    assert [block["tool_use_id"] for block in results] == ["call_a", "call_b"]


_IMAGE_TOOL_OUTPUT = [{"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "high"}]


def test_responses_to_chat_keeps_image_tool_output_as_image_part():
    body = {
        "model": "deepseek-flash",
        "input": [
            {"type": "function_call", "call_id": "call_img", "name": "view_image", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_img", "output": _IMAGE_TOOL_OUTPUT},
        ],
    }

    tool_message = opencode_proxy.responses_to_chat(body)["messages"][1]

    assert tool_message["role"] == "tool"
    content = tool_message["content"]
    assert isinstance(content, list)
    assert content == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]


def test_responses_to_messages_keeps_image_tool_output_as_image_block():
    body = {
        "model": "claude-x",
        "input": [
            {"type": "function_call", "call_id": "call_img", "name": "view_image", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_img", "output": _IMAGE_TOOL_OUTPUT},
        ],
    }

    result = opencode_proxy.responses_to_messages(body)["messages"][1]["content"][0]

    assert result["type"] == "tool_result"
    assert result["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    ]
