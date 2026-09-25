"""Ask a model for JSON that matches a pydantic model, with one repair attempt.

Extraction of a vacancy and extraction of a CV are the same problem: send a text
plus a schema, parse the reply, and if it does not validate, show the model its
own answer with the exact errors and let it try again. Models are good at fixing
a mistake once they are told what it is.

Two modes, same prompt:
- "schema": the server constrains decoding so the output must match the schema
  (shape guaranteed, content not).
- "prompt": we only ask for JSON; the model may add text around it or break the
  shape.

The caller supplies the system prompt, because what "correct" means is specific
to what is being read -- Dutch salary notation for a vacancy, dates and study
programmes for a CV. Everything below that is shared.
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from joblens.config import LLMSettings
from joblens.llm.providers import supports_json_schema
from joblens.llm.types import ChatClient, ChatResult, Message

Mode = Literal["schema", "prompt"]

# A vacancy needs ~300 output tokens, or ~1,500 with thinking; a CV profile with
# several jobs and a skill list needs more, so the cap is generous. It exists to
# stop a model that loops in its reasoning (qwen3 does at temperature 0) from
# blocking the server until its context is full, not to keep answers short.
MAX_OUTPUT_TOKENS = 3000

REPAIR_PROMPT = """\
Your JSON was not valid:
{errors}

Check these fields against the text again. If the text does not state a value,
use null instead of a placeholder or a guess.
Return the corrected JSON object only."""


class StructuredResult[T: BaseModel](BaseModel):
    """What came out, and what it cost to get it."""

    details: T
    mode: Mode
    attempts: int
    prompt_tokens: int
    output_tokens: int  # answer + reasoning, summed over attempts
    latency_s: float


class StructuredError(Exception):
    def __init__(self, message: str, attempts: list[ChatResult]):
        super().__init__(message)
        self.attempts = attempts  # the raw replies, for debugging


def default_mode(settings: LLMSettings) -> Mode:
    return "schema" if supports_json_schema(settings) else "prompt"


def extract_structured[T: BaseModel](
    text: str,
    client: ChatClient,
    *,
    schema: type[T],
    system_prompt: str,
    mode: Mode = "schema",
    max_attempts: int = 2,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    temperature: float = 0.0,
) -> StructuredResult[T]:
    """`system_prompt` must contain `{schema}`: the JSON schema goes in there.

    The field descriptions on the pydantic model are part of that schema, so they
    reach the model as per-field instructions rather than as documentation.

    `temperature` is 0 for extraction, which wants the same answer twice. The
    judge measures what a different value does (6.2): Google recommends 1.0 for
    every Gemini 3 model and warns that lower values can degrade reasoning.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    json_schema = schema.model_json_schema()
    instructions = system_prompt.format(schema=json.dumps(json_schema))
    messages: list[Message] = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": text},
    ]
    response_format = (
        {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": json_schema,
                "strict": True,
            },
        }
        if mode == "schema"
        else None
    )

    attempts: list[ChatResult] = []
    for _ in range(max_attempts):
        result = client.chat(
            messages,
            temperature=temperature,
            response_format=response_format,
            max_tokens=max_tokens,
        )
        attempts.append(result)
        if result.finish_reason == "length":
            # Cut-off JSON can't be repaired: asking again would restart the loop.
            raise StructuredError(
                f"Output limit of {max_tokens} tokens reached; the answer was cut "
                "off (the model probably looped in its reasoning).",
                attempts,
            )
        try:
            details = schema.model_validate_json(_json_text(result.content))
        except ValidationError as err:
            # Show the model its own answer and what was wrong with it.
            messages = [
                *messages,
                {"role": "assistant", "content": result.content},
                {"role": "user", "content": REPAIR_PROMPT.format(errors=_errors(err))},
            ]
            last_error = err
            continue
        return StructuredResult[T](
            details=details,
            mode=mode,
            attempts=len(attempts),
            prompt_tokens=sum(a.usage.prompt_tokens for a in attempts if a.usage),
            output_tokens=sum(a.usage.output_tokens for a in attempts if a.usage),
            latency_s=sum(a.latency_s for a in attempts),
        )

    errors = _errors(last_error)
    raise StructuredError(
        f"No valid {schema.__name__} after {max_attempts} attempts:\n{errors}", attempts
    )


def _json_text(content: str) -> str:
    """Cut the JSON object out of a reply like 'Here you go: ```json {...} ```'."""
    start, end = content.find("{"), content.rfind("}")
    return content[start : end + 1] if start != -1 and end > start else content


def _errors(err: ValidationError, limit: int = 10) -> str:
    lines = [
        f"- {'.'.join(str(p) for p in e['loc']) or 'object'}: {e['msg']}"
        for e in err.errors(include_url=False)[:limit]
    ]
    return "\n".join(lines)
