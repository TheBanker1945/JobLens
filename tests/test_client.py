"""Tests for the SDK client, against a fake server: no network, no real model.

The openai SDK uses httpx2 (a dependency of openai) instead of httpx, so the fake
server is built with httpx2.MockTransport.
"""

import json

import httpx
import httpx2
import openai
import pytest

from joblens.config import LLMSettings
from joblens.llm import raw_client
from joblens.llm.client import LLMClient

SETTINGS = LLMSettings(
    provider="ollama",
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="qwen3:8b",
)
MESSAGES = [{"role": "user", "content": "Zeg hallo."}]


def ollama_reply(content="Hallo", reasoning=None, reasoning_key="reasoning"):
    message = {"role": "assistant", "content": content}
    if reasoning:
        message[reasoning_key] = reasoning
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "qwen3:8b",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 2, "total_tokens": 27},
    }


def sdk_client(reply: dict, status: int = 200, seen: list | None = None, **kwargs):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return httpx2.Response(status, json=reply)

    fake = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return LLMClient(SETTINGS, http_client=fake, **kwargs)


def test_sends_same_request_body_as_raw_client():
    seen_sdk, seen_raw = [], []

    sdk_client(ollama_reply(), seen=seen_sdk).chat(MESSAGES)

    def raw_handler(request):
        seen_raw.append(request)
        return httpx.Response(200, json=ollama_reply())

    raw_client.chat(
        MESSAGES,
        SETTINGS,
        client=httpx.Client(transport=httpx.MockTransport(raw_handler)),
    )

    sdk_request, raw_request = seen_sdk[0], seen_raw[0]
    assert str(sdk_request.url) == str(raw_request.url)
    assert json.loads(sdk_request.content) == json.loads(raw_request.content)
    assert sdk_request.headers["Authorization"] == "Bearer ollama"


def test_parses_answer_usage_and_reasoning():
    reply = ollama_reply(reasoning="De gebruiker wil...")
    result = sdk_client(reply).chat(MESSAGES)

    assert result.content == "Hallo"
    assert result.reasoning == "De gebruiker wil..."
    assert result.usage.total_tokens == 27
    assert result.model == "qwen3:8b"


def test_reads_deepseek_style_reasoning_content():
    reply = ollama_reply(reasoning="Denken...", reasoning_key="reasoning_content")

    assert sdk_client(reply).chat(MESSAGES).reasoning == "Denken..."


def test_retries_server_errors_then_raises():
    seen = []
    client = sdk_client({"error": "overloaded"}, status=503, seen=seen, max_retries=1)

    with pytest.raises(openai.InternalServerError):
        client.chat(MESSAGES)
    assert len(seen) == 2  # first try + 1 retry


def test_auth_error_is_not_retried():
    seen = []
    client = sdk_client({"error": "bad key"}, status=401, seen=seen)

    with pytest.raises(openai.AuthenticationError):
        client.chat(MESSAGES)
    assert len(seen) == 1  # retrying a wrong key never helps


def test_response_format_is_sent_only_when_given():
    seen = []
    client = sdk_client(ollama_reply(), seen=seen)
    schema_format = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}

    client.chat(MESSAGES)
    client.chat(MESSAGES, response_format=schema_format)

    assert "response_format" not in json.loads(seen[0].content)
    assert json.loads(seen[1].content)["response_format"] == schema_format
