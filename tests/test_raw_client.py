"""Tests for the raw client, against a fake server: no network, no real model."""

import json

import httpx
import pytest

from joblens.config import LLMSettings
from joblens.llm.raw_client import chat

SETTINGS = LLMSettings(
    provider="ollama",
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="qwen3:8b",
)
MESSAGES = [{"role": "user", "content": "Zeg hallo."}]


def fake_client(reply: dict, status: int = 200, seen: list | None = None):
    """An httpx client whose 'server' records the request and returns `reply`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=reply)

    return httpx.Client(transport=httpx.MockTransport(handler))


def ollama_reply(content="Hallo", reasoning=None, reasoning_key="reasoning"):
    message = {"role": "assistant", "content": content}
    if reasoning:
        message[reasoning_key] = reasoning
    return {
        "model": "qwen3:8b",
        "choices": [{"index": 0, "message": message}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 2, "total_tokens": 27},
    }


def test_sends_openai_compatible_request():
    seen = []
    chat(MESSAGES, SETTINGS, client=fake_client(ollama_reply(), seen=seen))

    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer ollama"
    assert body["model"] == "qwen3:8b"
    assert body["messages"] == MESSAGES
    assert body["temperature"] == 0.0
    assert body["reasoning_effort"] == "none"  # thinking off by default


def test_thinking_on_leaves_reasoning_effort_out():
    seen = []
    settings = SETTINGS.model_copy(update={"thinking": True})
    chat(MESSAGES, settings, client=fake_client(ollama_reply(), seen=seen))

    assert "reasoning_effort" not in json.loads(seen[0].content)


def test_parses_answer_and_usage():
    result = chat(MESSAGES, SETTINGS, client=fake_client(ollama_reply()))

    assert result.content == "Hallo"
    assert result.reasoning is None
    assert result.usage.completion_tokens == 2
    assert result.latency_s >= 0


@pytest.mark.parametrize("key", ["reasoning", "reasoning_content"])
def test_reads_reasoning_under_either_name(key):
    reply = ollama_reply(reasoning="De gebruiker wil...", reasoning_key=key)
    result = chat(MESSAGES, SETTINGS, client=fake_client(reply))

    assert result.reasoning == "De gebruiker wil..."


def test_http_error_is_raised():
    client = fake_client({"error": "model not found"}, status=404)

    with pytest.raises(httpx.HTTPStatusError):
        chat(MESSAGES, SETTINGS, client=client)
