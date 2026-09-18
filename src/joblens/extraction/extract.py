"""Vacancy text -> validated VacancyDetails, with one repair attempt.

Flow: prompt (rules + JSON schema) -> LLM -> parse and validate with pydantic.
If validation fails, the model gets its own output back plus the exact errors and
tries again. Models are good at fixing a mistake once they are told what it is.

Two modes, same prompt:
- "schema": the server constrains decoding so the output must match the schema
  (shape guaranteed, content not).
- "prompt": we only ask for JSON; the model may add text around it or break the shape.
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from joblens.config import LLMSettings
from joblens.extraction.schema import VacancyDetails
from joblens.llm.providers import supports_json_schema
from joblens.llm.types import ChatClient, ChatResult, Message

Mode = Literal["schema", "prompt"]

SYSTEM_PROMPT = """\
You extract structured data from job vacancies (usually Dutch, sometimes English).

Rules:
- Use only information stated in the text. If something is not stated, use null
  (or [] for lists). Never guess.
- Dutch number format: "." separates thousands and "," decimals.
  "€ 3.200" -> 3200, "€ 14,85" -> 14.85.
- Enum fields must use the exact English values from the schema.
- Follow each field's description in the schema.

Return one JSON object matching this JSON schema:
{schema}"""

REPAIR_PROMPT = """\
Your JSON was not valid:
{errors}

Check these fields against the vacancy text again. If the text does not state a
value, use null instead of a placeholder or a guess.
Return the corrected JSON object only."""


class ExtractionResult(BaseModel):
    details: VacancyDetails
    mode: Mode
    attempts: int
    prompt_tokens: int
    completion_tokens: int
    latency_s: float


class ExtractionError(Exception):
    def __init__(self, message: str, attempts: list[ChatResult]):
        super().__init__(message)
        self.attempts = attempts  # the raw replies, for debugging


def default_mode(settings: LLMSettings) -> Mode:
    return "schema" if supports_json_schema(settings) else "prompt"


def extract_vacancy(
    text: str,
    client: ChatClient,
    *,
    mode: Mode = "schema",
    max_attempts: int = 2,
) -> ExtractionResult:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    schema = VacancyDetails.model_json_schema()
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(schema=json.dumps(schema))},
        {"role": "user", "content": text},
    ]
    response_format = (
        {
            "type": "json_schema",
            "json_schema": {"name": "VacancyDetails", "schema": schema, "strict": True},
        }
        if mode == "schema"
        else None
    )

    attempts: list[ChatResult] = []
    for _ in range(max_attempts):
        result = client.chat(messages, temperature=0.0, response_format=response_format)
        attempts.append(result)
        try:
            details = VacancyDetails.model_validate_json(_json_text(result.content))
        except ValidationError as err:
            # Show the model its own answer and what was wrong with it.
            messages = [
                *messages,
                {"role": "assistant", "content": result.content},
                {"role": "user", "content": REPAIR_PROMPT.format(errors=_errors(err))},
            ]
            last_error = err
            continue
        return ExtractionResult(
            details=details,
            mode=mode,
            attempts=len(attempts),
            prompt_tokens=sum(a.usage.prompt_tokens for a in attempts if a.usage),
            completion_tokens=sum(
                a.usage.completion_tokens for a in attempts if a.usage
            ),
            latency_s=sum(a.latency_s for a in attempts),
        )

    errors = _errors(last_error)
    raise ExtractionError(
        f"No valid VacancyDetails after {max_attempts} attempts:\n{errors}", attempts
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
