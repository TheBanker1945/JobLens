"""Shared shapes for every LLM client: what goes in, what comes out."""

from typing import Protocol

from pydantic import BaseModel

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": ...}


class Usage(BaseModel):
    prompt_tokens: int  # input: everything we sent
    completion_tokens: int  # output as reported by the provider
    total_tokens: int
    # Reasoning tokens the provider left OUT of completion_tokens (Gemini does this).
    # They are still billed as output.
    hidden_reasoning_tokens: int = 0

    @property
    def output_tokens(self) -> int:
        """Everything billed as output: answer + reasoning, on every provider."""
        return self.completion_tokens + self.hidden_reasoning_tokens

    @classmethod
    def from_api(cls, raw: dict) -> "Usage":
        """Normalise a provider's usage block. OpenAI and Ollama count reasoning
        inside completion_tokens (total = input + output); Gemini does not, so the
        gap between total and input + output is hidden reasoning."""
        prompt, completion = raw["prompt_tokens"], raw["completion_tokens"]
        total = raw.get("total_tokens") or prompt + completion
        return cls(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            hidden_reasoning_tokens=max(total - prompt - completion, 0),
        )


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
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        response_format: dict | None = None,
    ) -> ChatResult: ...
