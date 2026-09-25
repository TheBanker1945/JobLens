"""What a vacancy asks for, one line at a time, and how a CV answers each line.

The holistic judge (`judge.py`) reads a CV and a vacancy and writes a verdict
and a number in one go. 6.2 measured that number twice on the same 24 labelled
pairs of the real CV and got a concordance of 0.75 and then 0.84: the verdicts
hardly moved, but the fit inside `weak` wobbles by five points, and the
vacancies Mahdi would apply to and the ones he would not both live between 15
and 32. A number written in one go is the one part of the answer nobody can
check.

This judge never writes one. It asks two questions and adds up in code:

1. **What does the vacancy ask for?** Once per vacancy, before any CV is read,
   and stored. Each requirement carries the advert's own words (checked like
   every other quote), what kind of thing it is, whether it is a must, and
   whether it is a knockout. How much a requirement matters is decided before
   the CV is seen on purpose: a model that has just found the CV meets
   something rates it as more important, and a requirement's weight should not
   depend on who is reading it.
2. **Does this CV meet each one?** met, partly or missing, with the CV's words
   for every met or partly. A quote that is not in the CV turns the answer
   into missing. In 3.6 a verdict survived the deletion of the quote it rested
   on; here the deletion moves the number.

`score` then adds it up with the weights below, each next to its reason, and
the verdict is read off the number. The weights were written before the eval
ran and are not fitted to anyone's labels: five CVs are too few to fit
anything, and a weight fitted to Mahdi's answers would be a rule inferred from
them. How far a *person* will stretch past a stated requirement is theirs to
say (the preferences in 4.5); `Weights` is where that answer will go.

The output is a `MatchJudgement`, so the run file, the gap report, the refusal
and the viewer do not know which judge wrote it.
"""

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from joblens.cv.judge import (
    BANDS,
    DroppedQuote,
    Evidence,
    Gap,
    Judged,
    MatchJudgement,
    Verdict,
    shown_vacancy,
)
from joblens.cv.match import CVMatch
from joblens.cv.store import CVCache
from joblens.cv.verify import quoted, searchable
from joblens.llm.structured import Mode, extract_structured
from joblens.llm.types import ChatClient

# Stamped like PROMPT_VERSION, and two of them, because the two questions change
# apart: a vacancy's list is stored under LIST_VERSION and re-read for every CV,
# so a change to the second question must not throw the lists away. Bump
# LIST_VERSION when REQUIREMENTS_PROMPT changes, CHECK_VERSION when ASSESS_PROMPT
# or the weights do.
LIST_VERSION = "6.3"
CHECK_VERSION = "6.3.1"
REQUIREMENTS_VERSION = f"{LIST_VERSION}/{CHECK_VERSION}"

KNOCKOUT = (
    "true only if this rules a person out however well the rest fits, because "
    "applying cannot close it: a registration or licence the work legally needs "
    "(BIG, a driving licence for a driving job), the right to work, being "
    "enrolled as a student when the job is for students, a language at the "
    "level the vacancy states. Years of experience, a degree or 'hbo werk- en "
    "denkniveau', seniority and tools are never a knockout."
)


class Kind(StrEnum):
    SKILL = "skill"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    LANGUAGE = "language"
    ELIGIBILITY = "eligibility"
    OTHER = "other"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="What is asked, in a few words: 'Python', '3 jaar ervaring als "
        "full-stack developer', 'hbo werk- en denkniveau'."
    )
    quote: str = Field(
        description="The words from the vacancy that ask for it, copied EXACTLY, "
        "at most 25 words."
    )
    kind: Kind = Field(
        description="skill: a tool, technology, method or field of knowledge. "
        "experience: a kind of experience, a number of years, or a seniority. "
        "education: a degree, a level, or a 'denkniveau'. language: a language to "
        "speak or write. eligibility: a registration, licence, right to work, "
        "student status, or a condition on location or travel. other: anything "
        "else."
    )
    must: bool = Field(
        description="true if the vacancy states it as a requirement ('vereist', "
        "'minimaal', 'je hebt', 'you have', 'must'). false for a nice-to-have "
        "('pré', 'een plus', 'mooi meegenomen', 'bij voorkeur', 'nice to have') and "
        "for a tool or task only named in what the job involves ('je werkt met', "
        "'wat ga je doen', 'you will work with')."
    )
    knockout: bool = Field(description=KNOCKOUT)


class VacancyRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[Requirement] = Field(
        description="Everything the vacancy asks a candidate to have or be, the "
        "most important first, at most 12. A list in one sentence is several "
        "requirements: 'C#, Python en TypeScript' is three. Leave out what the "
        "employer offers, and personality and soft skills ('teamspeler', "
        "'communicatief sterk', 'enthousiast'): a CV cannot prove them and they "
        "separate nobody."
    )

    @model_validator(mode="after")
    def _a_knockout_is_a_must(self) -> Self:
        for one in self.requirements:
            if one.knockout and not one.must:
                raise ValueError(
                    f"{one.name!r} is a knockout and not a must: a nice-to-have "
                    "cannot rule anyone out"
                )
        return self


REQUIREMENTS_PROMPT = """\
You list what one vacancy asks of a candidate. It may be Dutch or English. You do
not see any CV: decide how much each requirement matters from the vacancy alone.

Rules:
- **Every quote must be copied exactly** from the vacancy, character for character.
  Quotes are checked afterwards and a requirement whose quote is not there is
  deleted.
- Only what the vacancy states. Never add a requirement it does not name.
- "hbo werk- en denkniveau" and "hbo-denkniveau" describe a level of working and
  thinking, not a diploma; list them as education, and they are never a knockout.
- A tool named only in what the job involves ("je werkt met", "jouw taken") is a
  requirement with must false.
- Knockouts are few; see the schema.

Return one JSON object matching this JSON schema:
{schema}"""


class Status(StrEnum):
    MET = "met"
    PARTLY = "partly"
    MISSING = "missing"
    # Only for a knockout the CV is silent about. The first run of this judge
    # on the real CV answered "missing" to "EU citizenship or a Dutch work
    # permit" -- which no CV states and this one did not -- and ruled out two
    # vacancies Mahdi would apply to. Not crediting what a CV does not say is
    # the rule; ruling someone out on what it does not say is the same mistake
    # the other way round.
    UNKNOWN = "unknown"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: int = Field(description="The requirement's number in the list.")
    cv_quote: str = Field(
        description="For met or partly: the words from the CV that show it, copied "
        "EXACTLY, at most 25 words. For missing: an empty string."
    )
    status: Status = Field(
        description="met: the CV states it. partly: the CV shows part of it or its "
        "close neighbour (fewer years of the right work, a related tool of the "
        "same kind, a related degree). missing: the CV does not show it. Never met "
        "or partly for something the CV does not state. unknown: only for a "
        "KNOCKOUT the CV is silent about (the right to work, a registration): "
        "a knockout is missing only when the CV shows it is not met."
    )


class Conflict(BaseModel):
    """A wish the CV states that the vacancy contradicts.

    Not a requirement the person fails but one the vacancy fails: "regio
    Tilburg" against a job in Geleen, "fulltime" against 16 uur. 6.2 found the
    lenient prompt promoting exactly these to strong, so they have their own
    field, both sides quoted and both quotes checked.
    """

    model_config = ConfigDict(extra="forbid")

    wish: str = Field(
        description="What the CV says this person wants, in a few words: 'regio "
        "Tilburg', 'fulltime', 'vast contract'."
    )
    cv_quote: str = Field(
        description="The CV's words that state the wish, copied EXACTLY, at most "
        "25 words."
    )
    vacancy_quote: str = Field(
        description="The vacancy's words that contradict it, copied EXACTLY, at "
        "most 25 words."
    )


class Work(StrEnum):
    SAME = "same"
    NEXT = "next"
    DIFFERENT = "different"


class RequirementJudgement(BaseModel):
    """The CV's answer to each requirement, then the kind of work, then a line.

    Quotes before statuses and the work after the answers, for the reason
    `MatchJudgement` gives: the model writes in schema order.
    """

    model_config = ConfigDict(extra="forbid")

    answers: list[Answer] = Field(
        description="One per requirement, in the order of the list."
    )
    conflicts: list[Conflict] = Field(
        description="Wishes the CV itself states (a region, hours, a contract, a "
        "start date) that this vacancy contradicts. Only when both texts state "
        "it: a vacancy that names no city does not contradict a region. Usually "
        "empty."
    )
    work_quote: str = Field(
        description="The words from the CV that show what work this person does, "
        "copied EXACTLY, at most 25 words."
    )
    work: Work = Field(
        description="same: the vacancy is the kind of work the CV shows this person "
        "doing. next: a step they are ready for, a neighbouring role or the same "
        "work one level up. different: other work, however many words it shares "
        "with the CV."
    )
    summary: str = Field(
        description="One or two sentences, to the person: what decides this vacancy "
        "for them. Specific, no compliments, never their name."
    )


ASSESS_PROMPT = """\
You check one CV against the requirements of one vacancy. The requirements were
read from the vacancy before you saw the CV; do not change them or add to them.
Say, for each one, whether the CV shows it. Both texts may be Dutch or English.

Rules:
- **Every quote must be copied exactly** from the CV, character for character.
  Quotes are checked afterwards, and a met or partly whose quote is not in the CV
  is counted as missing.
- **Never credit what the CV does not state.** Not "some exposure to", not a skill
  implied by a job title. If the CV does not say it, it is missing.
- partly is for a real neighbour: fewer years of the right work, a related tool
  of the same kind, a related degree. Not for "could learn it".
- A KNOCKOUT is missing only when the CV shows it is not met: another level of
  study for a student job, no registration where the CV lists its diplomas. When
  the CV is simply silent -- the right to work is almost never on a CV -- answer
  unknown.
- A "?" in the CV marks a character that could not be read. Never guess what it
  was, and never guess a number of years from it.
- Judge the work, not the vocabulary: a sales job at a data company is not data
  work.
- A wish the CV states (a region, hours, a contract) that the vacancy contradicts
  is a conflict, quoted from both. Neither side's silence is a conflict.
- Write to the person, not about them.

Return one JSON object matching this JSON schema:
{schema}"""

ASSESS_TEMPLATE = """\
## The CV

{cv}

## The vacancy: {shown}

## Its requirements

{requirements}"""

MAX_OUTPUT_TOKENS = 2000


@dataclass(frozen=True)
class Weights:
    """What a met requirement is worth, by kind. Written before the eval ran.

    A must-have counts in full. Years and degrees count half: they are what the
    holistic judge rejected on and what people apply past, and prior experience
    predicts little (a meta-analysis over 11,785 people found r = .06 with job
    performance, Van Iddekinge et al. 2019). A nice-to-have counts a quarter.
    Eligibility is not weighed: a knockout decides on its own, and a location
    or travel condition is a preference, not a skill.

    These are the judge's defaults for anyone. A person who says "I apply up to
    two years short" or "I never apply without the degree" will set them.
    """

    must: float = 1.0
    stretch: float = 0.5  # a must-have of kind experience or education
    nice: float = 0.25
    met: float = 1.0
    partly: float = 0.5
    # The kind of work weighs as much as the requirements do not: it is the
    # first thing a person reads in a vacancy and the one a CV cannot argue.
    work_share: float = 0.3
    work_points: dict[Work, float] = field(
        default_factory=lambda: {Work.SAME: 1.0, Work.NEXT: 0.6, Work.DIFFERENT: 0.0}
    )

    def of(self, requirement: Requirement) -> float:
        if requirement.kind is Kind.ELIGIBILITY:  # decides as a knockout, or not at all
            return 0.0
        if not requirement.must:
            return self.nice
        if requirement.kind in (Kind.EXPERIENCE, Kind.EDUCATION):
            return self.stretch
        return self.must


DEFAULT_WEIGHTS = Weights()


def score(
    requirements: list[Requirement],
    statuses: list[Status],
    work: Work,
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    conflicts: int = 0,
) -> tuple[Verdict, int]:
    """The verdict and the fit, from the answers. Arithmetic, and all of it here.

    fit = 100 x (coverage of the weighted requirements, by 1 - work_share,
                 plus the kind of work, by work_share)

    The verdict is the band the fit falls in, except that a missing knockout or
    different work is `weak` whatever the number says, and a wish of the CV's
    that the vacancy contradicts keeps it from `strong`. The number is then held
    inside that band, so the list sorts the way the verdicts read.
    """
    total = earned = 0.0
    for requirement, status in zip(requirements, statuses, strict=True):
        weight = weights.of(requirement)
        total += weight
        if status is Status.MET:
            earned += weight * weights.met
        elif status is Status.PARTLY:
            earned += weight * weights.partly
    work_points = weights.work_points[work]
    # A vacancy that asks for nothing weighable is judged on the work alone.
    coverage = earned / total if total else work_points
    fit = round(
        100 * ((1 - weights.work_share) * coverage + weights.work_share * work_points)
    )

    ruled_out = any(
        requirement.knockout and status is Status.MISSING
        for requirement, status in zip(requirements, statuses, strict=True)
    )
    if ruled_out or work is Work.DIFFERENT:
        return Verdict.WEAK, min(fit, BANDS[Verdict.WEAK][1])
    if conflicts:
        fit = min(fit, BANDS[Verdict.POSSIBLE][1])
    for verdict in (Verdict.STRONG, Verdict.POSSIBLE):
        if fit >= BANDS[verdict][0]:
            return verdict, fit
    return Verdict.WEAK, fit


@dataclass(frozen=True)
class Listed:
    """A vacancy's requirements, with the ones whose quote was not found gone."""

    requirements: list[Requirement]
    dropped: list[Requirement]


class RequirementBook:
    """Every vacancy's requirements, read once and kept.

    A vacancy's text does not change, so its requirements are stored by that
    text and read again for every CV and every run. Thread-safe, because the
    judge asks from six threads at once and two of them can want the same
    vacancy.
    """

    def __init__(
        self, cache: CVCache, client: ChatClient, *, model: str, mode: Mode = "schema"
    ):
        self.cache, self.client, self.model, self.mode = cache, client, model, mode
        self._lock = threading.Lock()
        self._busy: dict[str, threading.Lock] = {}

    def of(self, match: CVMatch) -> Listed:
        shown = shown_vacancy(match)
        with self._lock:
            one = self._busy.setdefault(shown, threading.Lock())
        with one:  # the second thread asking for this vacancy waits for the first
            kind = f"requirements-{LIST_VERSION}"
            with self._lock:
                hit = self.cache.get(kind, self.model, shown)
            if hit is None:
                found = extract_structured(
                    shown,
                    self.client,
                    schema=VacancyRequirements,
                    system_prompt=REQUIREMENTS_PROMPT,
                    mode=self.mode,
                )
                hit = found.details.model_dump(mode="json")
                with self._lock:
                    self.cache.put(kind, self.model, shown, hit)
        return check_requirements(VacancyRequirements.model_validate(hit), shown)


def check_requirements(found: VacancyRequirements, shown: str) -> Listed:
    """Drop every requirement whose quote is not in the vacancy it was read from."""
    text = searchable(shown)
    listed = Listed([], [])
    for one in found.requirements:
        (listed.requirements if quoted(one.quote, text) else listed.dropped).append(one)
    return listed


def numbered(requirements: list[Requirement]) -> str:
    """The list as the second call reads it: number, must or nice, kind, quote."""
    lines = []
    for number, one in enumerate(requirements, 1):
        need = "must" if one.must else "nice to have"
        lines.append(f'{number}. {one.name} ({need}, {one.kind}): "{one.quote}"')
    return "\n".join(lines) or "(the vacancy names no requirement)"


def judge_by_requirements(
    cv_text: str,
    match: CVMatch,
    client: ChatClient,
    *,
    book: RequirementBook,
    mode: Mode = "schema",
    temperature: float = 0.0,
    weights: Weights = DEFAULT_WEIGHTS,
) -> Judged:
    """One vacancy: its requirements (stored), the CV's answers (one call), a sum."""
    listed = book.of(match)
    result = extract_structured(
        ASSESS_TEMPLATE.format(
            cv=cv_text,
            shown=shown_vacancy(match),
            requirements=numbered(listed.requirements),
        ),
        client,
        schema=RequirementJudgement,
        system_prompt=ASSESS_PROMPT,
        mode=mode,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=temperature,
    )
    judged = combine(result.details, listed, cv_text, match, weights)
    return Judged(
        match=judged.match,
        judgement=judged.judgement,
        dropped=judged.dropped,
        quotes=judged.quotes,
        raw=result.details,
        prompt_tokens=result.prompt_tokens,
        output_tokens=result.output_tokens,
        latency_s=result.latency_s,
    )


_SUFFIX = {
    Status.PARTLY: " (partly shown)",
    Status.UNKNOWN: " (your CV does not say)",
}


def combine(
    answer: RequirementJudgement,
    listed: Listed,
    cv_text: str,
    match: CVMatch,
    weights: Weights = DEFAULT_WEIGHTS,
) -> Judged:
    """Check the answer's quotes, score it, and write it as a `MatchJudgement`.

    Also what a stored answer goes through again when it is read back, so a
    change to the check or to the weights is measured without a new call.
    """
    cv = searchable(cv_text)
    by_number = {one.requirement: one for one in answer.answers}
    statuses: list[Status] = []
    evidence: list[Evidence] = []
    gaps: list[Gap] = []
    dropped: list[DroppedQuote] = []
    quotes = 0
    for number, requirement in enumerate(listed.requirements, 1):
        given = by_number.get(number)
        status = given.status if given else Status.MISSING
        if status is Status.UNKNOWN and not requirement.knockout:
            status = Status.MISSING  # silence about a skill is not having it
        if status in (Status.MET, Status.PARTLY):
            quotes += 1
            if not quoted(given.cv_quote, cv):
                # The honesty rule reaches the number: an answer whose proof is
                # not in the CV is an answer the CV did not give.
                dropped.append(DroppedQuote("cv", requirement.name, given.cv_quote))
                status = Status.MISSING
        statuses.append(status)
        if status in (Status.MET, Status.PARTLY):
            evidence.append(
                Evidence(requirement=requirement.name, cv_quote=given.cv_quote)
            )
        if status is not Status.MET:
            gaps.append(
                Gap(
                    requirement=requirement.name + _SUFFIX.get(status, ""),
                    vacancy_quote=requirement.quote,
                    required=requirement.must,
                    knockout=requirement.knockout and status is Status.MISSING,
                )
            )

    vacancy = searchable(shown_vacancy(match))
    conflicts = 0
    for conflict in answer.conflicts:
        quotes += 2
        if not quoted(conflict.cv_quote, cv):
            dropped.append(DroppedQuote("cv", conflict.wish, conflict.cv_quote))
        elif not quoted(conflict.vacancy_quote, vacancy):
            dropped.append(
                DroppedQuote("vacancy", conflict.wish, conflict.vacancy_quote)
            )
        else:
            conflicts += 1
            gaps.append(
                Gap(
                    requirement=f"{conflict.wish} (a wish on your CV)",
                    vacancy_quote=conflict.vacancy_quote,
                    required=True,
                    knockout=False,
                )
            )

    verdict, fit = score(
        listed.requirements, statuses, answer.work, weights, conflicts=conflicts
    )
    # Validated like a model's answer: if the arithmetic ever put a fit outside
    # its band, or a knockout under anything but weak, this is where it shows.
    judgement = MatchJudgement(
        evidence=evidence,
        gaps=gaps,
        summary=answer.summary,
        verdict=verdict,
        fit=fit,
    )
    return Judged(
        match=match, judgement=judgement, dropped=dropped, quotes=quotes, raw=answer
    )
