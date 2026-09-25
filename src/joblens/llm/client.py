"""The main LLM client: the openai SDK, pointed at any OpenAI-compatible provider.

Compared with raw_client.py, the SDK adds retries with backoff, typed responses and
named errors (RateLimitError, AuthenticationError, ...). Its types only know OpenAI's
own fields, so provider extras like Ollama's `reasoning` arrive in `model_extra`.
"""

import time

from openai import DefaultHttpxClient, OpenAI, omit

from joblens.config import LLMSettings
from joblens.llm.providers import thinking_params
from joblens.llm.types import ChatResult, Message, Usage

# How often the SDK asks again after a connection error, a 429 or a 5xx, waiting
# 0.5, 1, 2, 4 and 8 seconds (its own backoff, plus jitter). The SDK's default
# is 2. Measured 2026-09-24 while Gemini answered "high demand" 503s: of 94
# calls, 18 succeeded only on the third to fifth retry -- calls that 2 would
# have lost -- and a batch run at 2 left 26% of its judge calls unanswered
# where 5 left 6%. A provider that is really down now takes about 15 seconds
# to say so. This is our own paid API being busy, not a website refusing a
# crawler: sources/polite.py never retries, and that rule is unchanged.
MAX_RETRIES = 5


class LLMClient:
    """Keeps one SDK client (and its open connections) for many calls."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        timeout: float = 120.0,  # the SDK's default is 600 s
        max_retries: int = MAX_RETRIES,  # connection errors, 429 and 5xx
        http_client: DefaultHttpxClient | None = None,  # tests pass a fake server
    ):
        self.settings = settings
        self._sdk = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )

    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        response_format: dict | None = None,  # e.g. a JSON schema to follow
        max_tokens: int | None = None,  # output cap, reasoning included
    ) -> ChatResult:
        start = time.perf_counter()
        response = self._sdk.chat.completions.create(
            model=self.settings.model,
            messages=messages,
            temperature=temperature,
            response_format=response_format or omit,
            max_tokens=max_tokens or omit,
            extra_body=thinking_params(self.settings),  # merged into the JSON body
        )
        latency = time.perf_counter() - start

        choice = response.choices[0]
        message = choice.message
        extra = message.model_extra or {}  # fields the SDK's types don't know about
        return ChatResult(
            content=message.content or "",
            reasoning=extra.get("reasoning") or extra.get("reasoning_content"),
            usage=Usage.from_api(response.usage.model_dump())
            if response.usage
            else None,
            model=response.model,
            latency_s=latency,
            finish_reason=choice.finish_reason,
        )

    def close(self) -> None:
        self._sdk.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
