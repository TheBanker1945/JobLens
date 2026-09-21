"""CV text -> validated CVProfile, on the shared extraction loop.

Same machinery as vacancy extraction (llm/structured.py): schema in the prompt,
one repair attempt when the answer does not validate. What differs is the rules,
because a CV lies in different places than a vacancy does -- it implies seniority
it cannot show, it lists a tool that appeared once in a course, and a model
reading it will happily smooth that over into a flattering summary.

The rule that matters most for what comes later: **only what the CV says.** Every
claim this app makes about a person has to be traceable to a line in their CV,
and that starts here. A profile with an invented skill in it poisons the match
report, the gap list and the eval at the same time.
"""

from joblens.cv.schema import CVProfile
from joblens.llm.structured import (
    Mode,
    StructuredError,
    StructuredResult,
    extract_structured,
)
from joblens.llm.types import ChatClient

CVResult = StructuredResult[CVProfile]
CVExtractionError = StructuredError

SYSTEM_PROMPT = """\
You read a CV (usually Dutch, sometimes English) and return structured data about
it. This is used to match the person against real job vacancies, so being wrong in
their favour is worse than leaving a field empty.

Rules:
- Use only what the CV states. If something is not stated, use null (or [] for
  lists). Never infer a skill from a job title, never infer a level from a study
  programme, and never add a technology because it usually goes with another one.
- Keep the CV's own wording for skills and titles. Do not translate them, do not
  expand abbreviations, do not tidy them up: the words the CV uses are the words
  that will be quoted back to this person.
- A skill named once, in a course or a hobby project, is still a skill the CV
  states. List it. Judging how strong it is happens later, not here.
- Do not write anything the CV does not contain. `summary` is the CV's own
  profile text, shortened. If there is none, it is null.
- Dates: "maart 2021" -> "2021-03", "2021" -> "2021", "heden"/"present" -> the job
  is current and `end` is null.
- Enum fields must use the exact values from the schema.
- Contact details have already been removed and appear as "[email removed]" and
  the like. Ignore those markers; they are not content.

Return one JSON object matching this JSON schema:
{schema}"""


def extract_cv(
    text: str,
    client: ChatClient,
    *,
    mode: Mode = "schema",
    max_attempts: int = 2,
) -> CVResult:
    return extract_structured(
        text,
        client,
        schema=CVProfile,
        system_prompt=SYSTEM_PROMPT,
        mode=mode,
        max_attempts=max_attempts,
    )
