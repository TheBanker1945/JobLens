"""Why does this vacancy fit, and what is missing? With quotes that are checked.

Retrieval says a vacancy is *close* to a CV. That is a number with no reasoning,
and the brief is blunt about what that is worth: "a bad answer is a number with
no reasoning, a summary that could describe anyone, or a compliment. Worst of all
is experience I do not have."

So the judge is asked for claims that carry their own evidence -- each one a quote
from the CV, each gap a quote from the vacancy -- and then **the quotes are
checked in code** against the two texts the model was given. A quote that is not
there loses its claim before anyone reads it. That check is the difference between
a prompt that asks for honesty and a program that enforces it: the prompt can be
ignored, `verify` cannot.

What is deliberately NOT done here: repairing a dropped quote by asking again. A
model that invented a line from a CV will happily invent a second one, and the
honest thing to show is a shorter list of claims that are all true.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from joblens.cv.match import CVMatch
from joblens.cv.verify import quoted, searchable
from joblens.llm.structured import Mode, StructuredError, extract_structured
from joblens.llm.types import ChatClient

# Stamped on every run. A judgement is comparable with another judgement only
# when the model, the rubric and this prompt are the same, so the prompt has a
# version and the report prints it. Bump it whenever SYSTEM_PROMPT or the bands
# change, and old numbers stop pretending to be comparable with new ones.
#
# 3.7 (6.2): the reasons come before the verdict, a knockout is a short list and
# marked on the gap, and years, degrees and seniority move the fit instead of
# deciding the verdict. docs/learning-log.md, 6.2, has the numbers.
PROMPT_VERSION = "3.7"


class Verdict(StrEnum):
    STRONG = "strong"  # a serious candidate: apply
    POSSIBLE = "possible"  # something real is missing, but it is worth a look
    WEAK = "weak"  # not this job


# The band each verdict has to agree with. A number on its own drifts -- "72"
# means whatever the model felt -- and a band on its own is too coarse to order
# twenty vacancies. Having both, and making them agree, means the number is
# anchored to a description in the prompt and the band is what you read.
BANDS: dict[Verdict, tuple[int, int]] = {
    Verdict.STRONG: (75, 100),
    Verdict.POSSIBLE: (40, 74),
    Verdict.WEAK: (0, 39),
}


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(description="What the vacancy asks for, in a few words.")
    cv_quote: str = Field(
        description="The words from the CV that answer it, copied EXACTLY as they "
        "appear, at most 25 words. Not a paraphrase, not a summary, not your own "
        "sentence. If no such line exists in the CV, leave this requirement out."
    )


class Gap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(
        description="What the vacancy asks for that the CV does not show."
    )
    vacancy_quote: str = Field(
        description="The words from the VACANCY where it asks for this, copied "
        "EXACTLY, at most 25 words."
    )
    required: bool = Field(
        description="true if the vacancy states this as a requirement ('vereist', "
        "'minimaal', 'je hebt', 'you have', 'must'), false if it is a nice-to-have "
        "('pré', 'een plus', 'mooi meegenomen', 'bij voorkeur', 'nice to have') or "
        "only describes the work ('je werkt met', 'wat ga je doen')."
    )
    knockout: bool = Field(
        description="true only if this rules the person out however well the rest "
        "fits, because applying cannot close it: a registration or licence the work "
        "legally needs (BIG, a driving licence for a driving job), the right to "
        "work, being enrolled as a student when the job is for students, a "
        "language at the level the vacancy states. Years of experience, a degree "
        "or 'hbo werk- en denkniveau', seniority and missing tools are never a "
        "knockout."
    )


class MatchJudgement(BaseModel):
    """The reasons first, the verdict last.

    A model writes its answer in the order the schema lists the fields, and
    Gemini keeps that order. In 3.6 the verdict and the number came first, so
    they were decided before a single piece of evidence had been written and
    the evidence could only justify them. Now the evidence and the gaps are
    written first and the verdict is read off them.
    """

    model_config = ConfigDict(extra="forbid")

    evidence: list[Evidence] = Field(
        description="At most 5, strongest first. Only what the CV actually states."
    )
    gaps: list[Gap] = Field(
        description="At most 5, most important first. What this vacancy asks for "
        "that the CV does not show. An empty list means the CV covers everything "
        "the vacancy asks -- rare, and you should be sure."
    )
    summary: str = Field(
        description="One or two sentences, honest and specific to this person and "
        "this vacancy. Name the thing that decides it. No compliments, no "
        "encouragement, nothing that could be said about any other candidate."
    )
    verdict: Verdict = Field(
        description="strong: this is the kind of work this person does or is ready "
        "for next, and what they lack is no more than they could talk around in an "
        "interview (a year or two less than asked, a neighbouring tool, a degree "
        "their work already covers). possible: the work fits but something real is "
        "missing (a core technology the job is built on, far more years than they "
        "have, a seniority clearly above theirs); worth applying as a long shot. "
        "weak: different work, or a knockout."
    )
    fit: int = Field(
        ge=0,
        le=100,
        description="0-100, and it must sit inside the verdict's band: strong "
        "75-100, possible 40-74, weak 0-39. High in the band when little is "
        "missing for that band, low when it only just belongs there. Use the "
        "range, not only round numbers.",
    )

    @model_validator(mode="after")
    def _fit_matches_verdict(self) -> Self:
        low, high = BANDS[self.verdict]
        if not low <= self.fit <= high:
            raise ValueError(
                f"fit {self.fit} is outside the {self.verdict} band ({low}-{high}); "
                "change one of the two so they agree"
            )
        return self

    @model_validator(mode="after")
    def _a_knockout_decides_the_verdict(self) -> Self:
        """The one gap that decides the verdict on its own has to be named as
        one, and a named one has to decide it: the rubric in the prompt, the
        check here, as with the bands."""
        knockouts = [gap for gap in self.gaps if gap.knockout]
        if any(not gap.required for gap in knockouts):
            raise ValueError(
                "a nice-to-have cannot be a knockout: set knockout to false, or "
                "required to true if the vacancy states it as a requirement"
            )
        if knockouts and self.verdict is not Verdict.WEAK:
            raise ValueError(
                f"a knockout gap ({knockouts[0].requirement!r}) makes the verdict "
                "weak; change the verdict, or set knockout to false if applying "
                "could close it"
            )
        return self


SYSTEM_PROMPT = """\
You judge whether one person should apply to one vacancy. You are given their CV
and the vacancy text, both of which may be Dutch or English.

You are not a recruiter screening a pile of candidates. You are a tool this person
uses to decide where to spend a limited number of applications, and you are more
useful when you disappoint them. People are hired with fewer years than an advert
asks and without its exact degree; they are not hired into work they cannot do.

Rules:
- **Every quote must be copied exactly** from the text you were given, character
  for character, in its original language. Quotes are checked against the source
  afterwards and a quote that cannot be found is deleted along with the claim it
  supported. A shorter honest list beats a longer one.
- **Never credit experience the CV does not state.** Not "some exposure to", not
  "likely familiar with", not a skill implied by a job title. If the CV does not
  say it, it is a gap.
- Judge the requirements, not the vocabulary. A vacancy full of words that also
  appear on the CV is not a match if the work is different (a sales job at a data
  company is not a data job).
- **Write the evidence and the gaps first, then decide.** The verdict and the fit
  follow from them.
- **Knockouts are few.** Only a requirement that applying cannot close rules the
  person out on its own: a registration or licence the work legally needs, the
  right to work, being a student when the job is for students, a language at the
  stated level. Mark that gap `knockout` and the verdict is weak. Say which one.
- **Years, degrees and seniority are a stretch, not a knockout.** Name them as
  gaps and weigh them: a year or two short in work this person already does costs
  little; twice their experience or a senior role costs a lot. They move the fit
  and can make the verdict possible; on their own they do not make it weak.
- **Dutch adverts:** "hbo werk- en denkniveau" and "hbo-denkniveau" describe a
  level of working and thinking, not a diploma, and experience can show it. Lines
  about the work itself ("je werkt met", "wat ga je doen", "jouw taken") describe
  the job, not a requirement: a tool named only there is a nice-to-have.
- **A list is several requirements.** For "je hebt ervaring met C#, Python en
  TypeScript", credit each one the CV shows as evidence and list only the missing
  ones as a gap.
- Distance and hours matter when both sides state them, and not otherwise.
- `weak` is a normal answer. A list where everything is strong tells them nothing.
- Write to the person, not about them: "je hebt" / "your CV shows", never their
  name and never the third person.

Return one JSON object matching this JSON schema:
{schema}"""

USER_TEMPLATE = """\
## The CV

{cv}

## The vacancy: {title}{where}

{vacancy}"""

# Reasoning about one vacancy needs a few hundred tokens of output; the cap is
# here for the same reason as in extraction, to stop a looping model rather than
# to keep the answer short.
MAX_OUTPUT_TOKENS = 1500


# The CV is identical in every call of a run and the vacancy is not, so the CV
# goes first: providers that cache a shared prefix can then reuse it across the
# whole shortlist.
def judge_match(
    cv_text: str,
    match: CVMatch,
    client: ChatClient,
    *,
    mode: Mode = "schema",
    temperature: float = 0.0,
) -> "Judged":
    """One vacancy, one call, and the quotes checked before it comes back."""
    vacancy = match.vacancy
    where = _where(match)
    prompt = USER_TEMPLATE.format(
        cv=cv_text, title=vacancy.title, where=where, vacancy=vacancy.text
    )
    result = extract_structured(
        prompt,
        client,
        schema=MatchJudgement,
        system_prompt=SYSTEM_PROMPT,
        mode=mode,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=temperature,
    )
    checked = verify(result.details, cv_text, shown_vacancy(match))
    return Judged(
        match=match,
        judgement=checked.judgement,
        dropped=checked.dropped,
        quotes=checked.quotes,
        raw=result.details,
        prompt_tokens=result.prompt_tokens,
        output_tokens=result.output_tokens,
        latency_s=result.latency_s,
    )


def shown_vacancy(match: CVMatch) -> str:
    """The vacancy exactly as the judge was shown it: its heading line, then the
    advert. What a gap quote is checked against.

    Until 6.2 only the advert was, and the only two quotes dropped over 751 in
    6.1's baseline were the model quoting the heading -- "Medior /Senior Java
    Software Engineer (Keylane · Utrecht)" -- which it had been given. Checking
    the text the model saw is not a loosening: nothing it was not shown passes.
    The "## The vacancy:" label is ours and is left out, so it cannot be quoted.
    """
    return f"{match.vacancy.title}{_where(match)}\n\n{match.vacancy.text}"


def _where(match: CVMatch) -> str:
    vacancy = match.vacancy
    parts = [p for p in (vacancy.company, vacancy.city) if p]
    return f" ({' · '.join(parts)})" if parts else ""


@dataclass(frozen=True)
class DroppedQuote:
    kind: str  # "cv" or "vacancy": which text it claimed to come from
    claim: str
    quote: str


@dataclass(frozen=True)
class Checked:
    judgement: MatchJudgement  # the claims whose quotes were found, and only those
    dropped: list[DroppedQuote]
    quotes: int  # how many were checked in total

    @property
    def faithfulness(self) -> float:
        """The share of quotes that were really in the source. 1.0 or a bug."""
        if not self.quotes:
            return 1.0
        return (self.quotes - len(self.dropped)) / self.quotes


def verify(judgement: MatchJudgement, cv_text: str, vacancy_text: str) -> Checked:
    """Drop every claim whose quote is not in the text it says it came from."""
    cv, vacancy = searchable(cv_text), searchable(vacancy_text)
    dropped: list[DroppedQuote] = []

    kept_evidence = []
    for item in judgement.evidence:
        if quoted(item.cv_quote, cv):
            kept_evidence.append(item)
        else:
            dropped.append(DroppedQuote("cv", item.requirement, item.cv_quote))

    kept_gaps = []
    for gap in judgement.gaps:
        if quoted(gap.vacancy_quote, vacancy):
            kept_gaps.append(gap)
        else:
            dropped.append(DroppedQuote("vacancy", gap.requirement, gap.vacancy_quote))

    return Checked(
        judgement=judgement.model_copy(
            update={"evidence": kept_evidence, "gaps": kept_gaps}
        ),
        dropped=dropped,
        quotes=len(judgement.evidence) + len(judgement.gaps),
    )


@dataclass(frozen=True)
class Judged:
    match: CVMatch
    judgement: MatchJudgement  # only the claims whose quotes were found
    dropped: list[DroppedQuote]
    quotes: int
    # What the model actually answered, before anything was taken out of it. Kept
    # because it is the thing worth storing: re-checking a saved answer costs
    # nothing, so a change to `verify` can be measured against old runs instead
    # of paying for new ones.
    raw: MatchJudgement | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    error: str | None = None

    @property
    def rank_key(self) -> tuple[int, int, float]:
        """How the list is ordered once it has been judged: the verdict first,
        then the number inside it, and the retrieval score only to break a tie.

        Retrieval decided which twenty vacancies were worth a call; it does not
        get to decide the order they are shown in.
        """
        order = {Verdict.STRONG: 2, Verdict.POSSIBLE: 1, Verdict.WEAK: 0}
        return (order[self.judgement.verdict], self.judgement.fit, self.match.score)


# Six at a time: enough to turn twenty sequential calls of two to four seconds
# into half a minute, and few enough that a provider's rate limit is not the next
# thing to debug.
WORKERS = 6


def judge_matches(
    cv_text: str,
    matches: list[CVMatch],
    client: ChatClient,
    *,
    mode: Mode = "schema",
    temperature: float = 0.0,
    workers: int = WORKERS,
    on_done=None,
    on_judged: Callable[[Judged], None] | None = None,
) -> tuple[list[Judged], list[str]]:
    """Judge a shortlist, best first. Returns what came back and what failed.

    One vacancy failing is not the run failing: nineteen judged vacancies are
    still worth reading, so the failure is collected and named rather than
    raised.

    `on_judged` receives each judgement as it arrives, in this thread, so a
    caller can store it before the next one lands: an eval that pays for a
    hundred calls should not lose all of them to the hundred-and-first.
    """
    return judge_pairs(
        [(cv_text, match) for match in matches],
        client,
        mode=mode,
        temperature=temperature,
        workers=workers,
        on_done=on_done,
        on_judged=on_judged,
    )


def judge_pairs(
    pairs: list[tuple[str, CVMatch]],
    client: ChatClient,
    *,
    mode: Mode = "schema",
    temperature: float = 0.0,
    workers: int = WORKERS,
    on_done=None,
    on_judged: Callable[[Judged], None] | None = None,
) -> tuple[list[Judged], list[str]]:
    """`judge_matches` for pairs that do not share a CV.

    A person's shortlist is one CV against many vacancies; an external benchmark
    is two hundred CVs against ten job descriptions. The call is the same.
    """
    judged: list[Judged] = []
    failures: list[str] = []

    def one(pair: tuple[str, CVMatch]):
        cv_text, match = pair
        return match, judge_match(
            cv_text, match, client, mode=mode, temperature=temperature
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for match, result in _as_completed(pool, pairs, one, failures):
            judged.append(result)
            if on_judged:
                on_judged(result)
            if on_done:
                on_done(len(judged) + len(failures), len(pairs), match)
    return sorted(judged, key=lambda j: j.rank_key, reverse=True), failures


def _as_completed(pool, pairs, work, failures):
    """Yield results as they arrive, so progress means what it says."""
    futures = {pool.submit(work, pair): pair[1] for pair in pairs}
    for future in as_completed(futures):
        match = futures[future]
        try:
            yield future.result()
        except StructuredError as err:
            failures.append(f"{match.vacancy.key}: {err}")
        except Exception as err:  # noqa: BLE001 - one vacancy must not stop the run
            failures.append(f"{match.vacancy.key}: {type(err).__name__}: {err}")
