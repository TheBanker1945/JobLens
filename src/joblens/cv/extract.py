"""CV text -> validated CVProfile, checked against the CV it came from.

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

from dataclasses import dataclass

from joblens.cv.schema import CVProfile
from joblens.cv.verify import searchable
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


# ---------------------------------------------------------------------------
# Checking the answer, not trusting it
#
# A model that is handed `Founder & Full-Stack Developer Dec ???? ? Sep ????`
# does not report a hole. On a real CV whose font had eaten every year (see
# cv/read.py) it answered "2024-12 - 2025-09" with no hedge, and two of the five
# periods it produced were wrong -- which then became a confident "1.1 years
# covered by listed jobs" in the report.
#
# The fix is the one 3.6 already uses on quotes: **ask the text, not the model**.
# A year in the profile has to appear in the CV, or the field becomes null. Same
# principle, same comparison (cv/verify.py), one step earlier in the pipeline.
#
# Only the year is checked, because only the year is checkable: a CV writes
# "Dec" and the schema wants "2024-12", so the month is a translation and the
# year is a copy. A year that is not there makes the whole field null, month
# included -- half a date is not a date.


@dataclass(frozen=True)
class DroppedDate:
    where: str  # the job or study it was on, so a person can see which one went
    field: str  # "start", "end" or "end_year"
    value: str


@dataclass(frozen=True)
class CheckedProfile:
    profile: CVProfile  # the same profile with every unsupported date nulled
    dropped: list[DroppedDate]
    dates: int  # how many were checked

    @property
    def faithfulness(self) -> float:
        """The share of dates that were really in the CV. 1.0 or a damaged CV."""
        if not self.dates:
            return 1.0
        return (self.dates - len(self.dropped)) / self.dates


def verify_dates(profile: CVProfile, cv_text: str) -> CheckedProfile:
    """Null every date in the profile whose year is not in the CV text."""
    text = searchable(cv_text)
    dropped: list[DroppedDate] = []
    dates = 0

    jobs = []
    for job in profile.experience:
        where = job.company or job.title
        update = {}
        for field in ("start", "end"):
            value = getattr(job, field)
            if value is None:
                continue
            dates += 1
            if not _year_in(value[:4], text):
                dropped.append(DroppedDate(where, field, value))
                update[field] = None
        jobs.append(job.model_copy(update=update) if update else job)

    studies = []
    for study in profile.education:
        if study.end_year is None:
            studies.append(study)
            continue
        dates += 1
        if _year_in(str(study.end_year), text):
            studies.append(study)
        else:
            dropped.append(
                DroppedDate(study.programme, "end_year", str(study.end_year))
            )
            studies.append(study.model_copy(update={"end_year": None}))

    checked = profile.model_copy(update={"experience": jobs, "education": studies})
    return CheckedProfile(profile=checked, dropped=dropped, dates=dates)


def _year_in(year: str, text: str) -> bool:
    """A CV writes a year in full ("2024") or short ("\'24"), and nothing else.

    Both are a copy of what is on the page rather than a reading of it, which is
    the whole point: no other spelling is accepted, because accepting a second
    spelling is how a check becomes a guess.
    """
    return year in text or f"'{year[2:]}" in text
