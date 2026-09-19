"""A chat completion call by hand: one HTTP POST, no SDK.

Every OpenAI-compatible provider (Ollama, OpenRouter, DeepSeek, ...) speaks the same
protocol: POST {base_url}/chat/completions with the model name and the full list of
messages, and get the answer back as JSON. The API is stateless, so the caller sends
the whole conversation on every call.
"""

import time

import httpx

from joblens.config import LLMSettings
from joblens.llm.providers import thinking_params
from joblens.llm.types import ChatResult, Message, Usage


def build_request_body(
    messages: list[Message], settings: LLMSettings, temperature: float
) -> dict:
    body = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
    }
    body.update(thinking_params(settings))  # provider-specific thinking switch
    return body


def chat(
    messages: list[Message],
    settings: LLMSettings,
    *,
    temperature: float = 0.0,
    timeout: float = 120.0,
    client: httpx.Client | None = None,
) -> ChatResult:
    """Send one chat completion request and return the parsed result.

    `client` can be passed in so tests can swap the network for a fake server.
    """
    if client is None:
        # We own this client, so close it when done (the `with` block does that).
        with httpx.Client(timeout=timeout) as owned:
            return chat(messages, settings, temperature=temperature, client=owned)

    start = time.perf_counter()
    response = client.post(
        f"{settings.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        json=build_request_body(messages, settings, temperature),
    )
    latency = time.perf_counter() - start
    response.raise_for_status()  # 4xx/5xx -> httpx.HTTPStatusError

    data = response.json()
    message = data["choices"][0]["message"]
    return ChatResult(
        content=message.get("content") or "",
        # Ollama calls it "reasoning", DeepSeek and vLLM "reasoning_content"
        reasoning=message.get("reasoning") or message.get("reasoning_content"),
        usage=Usage.from_api(data["usage"]) if data.get("usage") else None,
        model=data.get("model", settings.model),
        latency_s=latency,
    )
