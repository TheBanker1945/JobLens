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


class LLMClient:
    """Keeps one SDK client (and its open connections) for many calls."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        timeout: float = 120.0,  # the SDK's default is 600 s
        max_retries: int = 2,  # retries on connection errors, 429 and 5xx
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
    ) -> ChatResult:
        start = time.perf_counter()
        response = self._sdk.chat.completions.create(
            model=self.settings.model,
            messages=messages,
            temperature=temperature,
            response_format=response_format or omit,
            extra_body=thinking_params(self.settings),  # merged into the JSON body
        )
        latency = time.perf_counter() - start

        message = response.choices[0].message
        extra = message.model_extra or {}  # fields the SDK's types don't know about
        return ChatResult(
            content=message.content or "",
            reasoning=extra.get("reasoning") or extra.get("reasoning_content"),
            usage=Usage.from_api(response.usage.model_dump())
            if response.usage
            else None,
            model=response.model,
            latency_s=latency,
        )

    def close(self) -> None:
        self._sdk.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
