"""Vacancy text -> validated VacancyDetails.

The prompt, the JSON schema and the repair loop live in llm/structured.py, which
knows nothing about vacancies. What is here is what is specific to a vacancy:
the rules a model has to follow to read a Dutch job advert correctly.

`ExtractionResult` and `ExtractionError` are the names the rest of JobLens
already used before the loop was shared with CV reading; they are the generic
ones under a vacancy-shaped name.
"""

from joblens.extraction.schema import VacancyDetails
from joblens.llm.structured import (
    MAX_OUTPUT_TOKENS,
    Mode,
    StructuredError,
    StructuredResult,
    default_mode,
    extract_structured,
)
from joblens.llm.types import ChatClient

ExtractionResult = StructuredResult[VacancyDetails]
ExtractionError = StructuredError

__all__ = [
    "MAX_OUTPUT_TOKENS",
    "ExtractionError",
    "ExtractionResult",
    "Mode",
    "default_mode",
    "extract_vacancy",
]

SYSTEM_PROMPT = """\
You extract structured data from job vacancies (usually Dutch, sometimes English).

Rules:
- Use only information stated in the text. If something is not stated, use null
  (or [] for lists). Never guess, and never fill a value from your own knowledge:
  a pay scale without amounts ("schaal 11") means salary_min, salary_max and
  salary_period are null.
- Read the whole text, including the task description: skills and tools named
  there count too.
- Dutch number format: "." separates thousands and "," decimals.
  "€ 3.200" -> 3200, "€ 14,85" -> 14.85.
- Enum fields must use the exact English values from the schema.
- Follow each field's description in the schema.

Return one JSON object matching this JSON schema:
{schema}"""


def extract_vacancy(
    text: str,
    client: ChatClient,
    *,
    mode: Mode = "schema",
    max_attempts: int = 2,
) -> ExtractionResult:
    return extract_structured(
        text,
        client,
        schema=VacancyDetails,
        system_prompt=SYSTEM_PROMPT,
        mode=mode,
        max_attempts=max_attempts,
    )
