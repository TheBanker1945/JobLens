"""Shared shapes for every LLM client: what goes in, what comes out."""

from typing import Protocol

from pydantic import BaseModel

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": ...}


class Usage(BaseModel):
    prompt_tokens: int  # input: everything we sent
    completion_tokens: int  # output: answer + reasoning
    total_tokens: int


class ChatResult(BaseModel):
    content: str
    reasoning: str | None = None  # the model's "thinking", if it returned any
    usage: Usage | None = None
    model: str
    latency_s: float


class ChatClient(Protocol):
    """Anything that can answer a list of messages. The rest of JobLens depends on
    this, not on a specific client, so clients can be swapped (or faked in tests)."""

    def chat(
        self, messages: list[Message], *, temperature: float = 0.0
    ) -> ChatResult: ...
