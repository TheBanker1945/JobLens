"""A run, stored, so that two of them can be compared -- or honestly refused.

3.6 ended every report with a line of provenance:

    run: cv 9f1c2b84 · raw corpus of 279 · gemini-embedding-2 · gemini-3.8-flash
         · prompt 3.6 · 2026-09-22 14:30

It said what would have to be equal for two runs to mean the same thing, and then
threw it away with the terminal scrollback. Here it becomes a file, next to what
the run actually concluded, and the comparability rule becomes code.

**What the rule divides on.** Five things set the scale a verdict is measured in:
which CV was read, which model judged it, which prompt version it judged under,
which embedder chose the shortlist, and which corpus (samples or raw) it was
chosen from. If any of those differ, two numbers are not two measurements of the
same thing and `compare` refuses, naming which one moved.

**The contents of the corpus are deliberately not on that list.** They change
most days `daily_update.sh` runs -- 202 vacancies in 3.1, 279 today -- and making
that a blocker would refuse nearly every real pair of runs, including the one
question worth asking: is anything new better than last week's best? So a changed
corpus is reported rather than refused: how the size and the digest moved, which
vacancies entered and left the shortlist, and the verdicts compared over the ones
present in both. The honest thing is to compare and say what moved underneath, not
to stay silent.

Reading and writing one of these is not here: since 4.2 every store of a run, a
label or a preference goes through `joblens.storage`, so that a run is addressed
by an id rather than by a path and a hosted version has one thing to replace.
This module is the shape of a run and the rule about comparing two.
"""

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field

from joblens.corpus import Funnel
from joblens.cv.gaps import GapSummary
from joblens.cv.judge import Judged, Verdict
from joblens.cv.match import CVMatch
from joblens.cv.outcome import Fit, Outcome
from joblens.preferences.schema import Conflict
from joblens.sources.base import Vacancy

# Everything that has to be equal before two runs are two measurements of one
# thing. Named here rather than spelled out in `compare`, so the rule is a list
# you can read and not an `if` you have to reconstruct.
SCALE_FIELDS: dict[str, str] = {
    "cv_digest": "a different CV was read",
    "corpus": "a different corpus",
    "embed_model": "a different embedding model chose the shortlist",
    "judge_model": "a different model judged it",
    "prompt_version": "a different judge prompt",
    "cv_style": "the CV asked the index a different way",
}


class RunStamp(BaseModel):
    """Provenance. Two runs are comparable only if all of `SCALE_FIELDS` match."""

    cv_name: str
    cv_digest: str  # of the redacted text: the only version that left the machine
    corpus: str
    corpus_size: int
    corpus_digest: str  # of the sorted vacancy keys: "the corpus changed", checkable
    embed_model: str
    judge_model: str
    cv_style: str
    prompt_version: str
    top: int
    # At most this many judged per employer (cv/match.py); 0 is no cap, which
    # is also what every run from before the cap existed loads as.
    per_employer: int = 0
    # The preferences that moved the ranking and told the judge, as their
    # version and a digest of the answers ("p1:3f9a1c20"). "" for a run with
    # none, which is also what every run from before 7.3 loads as.
    preferences: str = ""
    at: datetime = Field(default_factory=datetime.now)

    def line(self) -> str:
        """The one-line form 3.6 printed, unchanged, so the report still reads."""
        return (
            f"cv {self.cv_digest} · {self.corpus} corpus of {self.corpus_size} "
            f"({self.corpus_digest}) · {self.embed_model} · {self.judge_model} · "
            f"prompt {self.prompt_version} · {self.at:%Y-%m-%d %H:%M}"
        )


class GapRow(BaseModel):
    requirement: str
    quote: str
    required: bool
    # Rules the person out on its own (6.2). False on runs stored before it,
    # which is what they meant: 3.6 had no way to say it.
    knockout: bool = False


class ClaimRow(BaseModel):
    """One line of the CV the judge used, after it was found in the CV."""

    requirement: str
    quote: str


class JudgedRow(BaseModel):
    key: str
    title: str
    company: str | None = None
    city: str | None = None
    url: str = ""
    score: float  # the retrieval cosine: what put it on the shortlist
    verdict: Verdict
    fit: int
    summary: str
    gaps: list[GapRow] = []
    evidence: int = 0  # how many claims survived the quote check
    dropped: int = 0
    # The claims themselves. Until the 2026-09-22 audit a run kept only the
    # count above, so the viewer -- where a vacancy is now marked -- could say
    # "3 claims verified" and not which lines of the CV they were. Empty on
    # runs stored before then.
    claims: list[ClaimRow] = []


class RankedRow(BaseModel):
    """Where retrieval put a vacancy, and whether that was near enough to be read.

    One of these per vacancy in the corpus, not per vacancy judged. A run used to
    store its twelve judgements and nothing else, so the other 267 rejections had
    no score, no position and no record that they had ever been considered -- and
    a rejection you cannot see is one you cannot argue with. The judgement of the
    head of this list is in `RunRecord.rows`, joined on `key`.

    The title and company are copied in rather than looked up later on purpose:
    daily_update.sh rewrites the corpus, boards take adverts down, and a stored
    run has to stay readable when the vacancy it names is gone. `JudgedRow` does
    the same, for the same reason.
    """

    rank: int  # 1 is the closest
    key: str
    title: str
    company: str | None = None
    city: str | None = None
    url: str = ""
    score: float  # the cosine it was ranked on
    part: str = ""  # which piece of the CV matched it best
    judged: bool = False  # was it sent to the judge? a failed call is still True
    # Ranked high enough to be judged, and skipped because its employer already
    # had `per_employer` on the shortlist. Not judged, and not "below the cut".
    capped: bool = False
    # Where retrieval put it before the person's preferences moved it (7.3),
    # and what they said that it contradicts. `before` is None on a run without
    # preferences; `conflicts` is empty for every vacancy that was not moved.
    before: int | None = None
    conflicts: list[Conflict] = []


class GroupRow(BaseModel):
    term: str
    count: int
    required_count: int
    weight: float


class RunRecord(BaseModel):
    """One `match_cv.py` run: what it was, what it found, what it cost."""

    stamp: RunStamp
    outcome: Fit
    rows: list[JudgedRow]
    # The whole corpus in order, judged or not. Empty on runs stored before 4.1.
    ranking: list[RankedRow] = []
    funnel: Funnel = Funnel()  # and what never reached the ranking at all
    groups: list[GroupRow] = []
    ungrouped: int = 0
    already_on_cv: int = 0
    failures: list[str] = []
    prompt_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0

    def by_key(self) -> dict[str, JudgedRow]:
        return {row.key: row for row in self.rows}

    def ranked_by_key(self) -> dict[str, RankedRow]:
        return {row.key: row for row in self.ranking}

    def boundary(self) -> tuple[RankedRow, RankedRow] | None:
        """The last vacancy that was read and the first that was not.

        The shortlist is a cut through a list of very close numbers -- on the
        first real run the twelve judged vacancies spanned 0.680 to 0.711 -- so
        the interesting thing about the cut is how little separates the two
        vacancies on either side of it. None when nothing was cut off.
        """
        last = next((row for row in reversed(self.ranking) if row.judged), None)
        first = next(
            (row for row in self.ranking if not row.judged and not row.capped), None
        )
        return (last, first) if last and first else None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def corpus_digest(vacancies: list[Vacancy]) -> str:
    """A fingerprint of which vacancies were in the pool, not how many.

    Two fetches can both land on 279 and not be the same 279, and "the corpus
    changed" is exactly the sort of thing a size alone lets you miss.
    """
    return digest("\n".join(sorted(vacancy.key for vacancy in vacancies)))


def build_record(
    stamp: RunStamp,
    judged: list[Judged],
    outcome: Outcome,
    summary: GapSummary | None = None,
    *,
    ranking: list[CVMatch] | None = None,
    shortlisted: int = 0,
    sent: set[str] | None = None,
    capped: set[str] | None = None,
    funnel: Funnel | None = None,
    failures: list[str] | None = None,
    cost_usd: float | None = None,
    before: dict[str, int] | None = None,
    conflicts: dict[str, list[Conflict]] | None = None,
) -> RunRecord:
    rows = [
        JudgedRow(
            key=one.match.vacancy.key,
            title=one.match.vacancy.title,
            company=one.match.vacancy.company,
            city=one.match.vacancy.city,
            url=one.match.vacancy.url,
            score=one.match.score,
            verdict=one.judgement.verdict,
            fit=one.judgement.fit,
            summary=one.judgement.summary,
            gaps=[
                GapRow(
                    requirement=gap.requirement,
                    quote=gap.vacancy_quote,
                    required=gap.required,
                    knockout=gap.knockout,
                )
                for gap in one.judgement.gaps
            ],
            evidence=len(one.judgement.evidence),
            dropped=len(one.dropped),
            claims=[
                ClaimRow(requirement=item.requirement, quote=item.cv_quote)
                for item in one.judgement.evidence
            ],
        )
        for one in judged
    ]
    # `shortlisted` and not "whichever ones came back": a vacancy whose judge
    # call failed was read, and lumping it in with the 267 that were never sent
    # would hide the failure behind the number it is least like. With a cap per
    # employer the ones sent are no longer the head of the ranking, so `sent`
    # names them by key; `shortlisted` counts the head, for callers without one.
    ranked = [
        RankedRow(
            rank=position,
            key=match.vacancy.key,
            title=match.vacancy.title,
            company=match.vacancy.company,
            city=match.vacancy.city,
            url=match.vacancy.url,
            score=match.score,
            part=match.part.label,
            judged=match.vacancy.key in sent
            if sent is not None
            else position <= shortlisted,
            capped=match.vacancy.key in (capped or set()),
            before=before.get(match.vacancy.key) if before else None,
            conflicts=(conflicts or {}).get(match.vacancy.key, []),
        )
        for position, match in enumerate(ranking or [], 1)
    ]
    return RunRecord(
        stamp=stamp,
        outcome=outcome.fit,
        rows=rows,
        ranking=ranked,
        funnel=funnel or Funnel(),
        groups=[
            GroupRow(
                term=group.term,
                count=group.count,
                required_count=group.required_count,
                weight=round(group.weight, 2),
            )
            for group in (summary.groups if summary else [])
        ],
        ungrouped=len(summary.ungrouped) if summary else 0,
        already_on_cv=len(summary.already_on_cv) if summary else 0,
        failures=failures or [],
        prompt_tokens=sum(one.prompt_tokens for one in judged),
        output_tokens=sum(one.output_tokens for one in judged),
        cost_usd=cost_usd,
        seconds=sum(one.latency_s for one in judged),
    )


class VerdictChange(BaseModel):
    key: str
    title: str
    before: Verdict
    after: Verdict
    fit_before: int
    fit_after: int


class Comparison(BaseModel):
    """Whether two runs can be compared, and what moved if they can."""

    blockers: list[str] = []  # why these two are not measurements of one thing
    notes: list[str] = []  # what changed underneath but does not block
    entered: list[JudgedRow] = []  # on the newer shortlist and not the older
    left: list[JudgedRow] = []  # the other way round
    changed: list[VerdictChange] = []
    unchanged: int = 0

    @property
    def comparable(self) -> bool:
        return not self.blockers


def compare(before: RunRecord, after: RunRecord) -> Comparison:
    """Two runs, oldest first. Refuses on scale, reports on content."""
    blockers = [
        f"{reason}: {getattr(before.stamp, name)} -> {getattr(after.stamp, name)}"
        for name, reason in SCALE_FIELDS.items()
        if getattr(before.stamp, name) != getattr(after.stamp, name)
    ]
    notes = []
    if before.stamp.corpus_digest != after.stamp.corpus_digest:
        notes.append(
            f"the corpus is not the same set of vacancies: "
            f"{before.stamp.corpus_size} ({before.stamp.corpus_digest}) -> "
            f"{after.stamp.corpus_size} ({after.stamp.corpus_digest})"
        )
    if before.stamp.top != after.stamp.top:
        notes.append(
            f"a different shortlist size: --top {before.stamp.top} -> "
            f"{after.stamp.top}, so a vacancy can leave the list without "
            f"anything about it changing"
        )
    if before.stamp.per_employer != after.stamp.per_employer:
        notes.append(
            f"a different cap per employer: {before.stamp.per_employer or 'none'} "
            f"-> {after.stamp.per_employer or 'none'}, so a vacancy can leave the "
            f"list because of whom else it was ranked with"
        )
    if before.stamp.preferences != after.stamp.preferences:
        # A note, not a blocker: "what did my preferences change?" is exactly
        # the comparison someone setting them wants. The judge was told
        # something different, so a changed verdict can be the preference
        # working rather than noise.
        notes.append(
            f"different preferences: {before.stamp.preferences or 'none'} -> "
            f"{after.stamp.preferences or 'none'}, so the shortlist moved and the "
            f"judge may have been told different rules"
        )
    if blockers:
        return Comparison(blockers=blockers, notes=notes)

    old, new = before.by_key(), after.by_key()
    changed = [
        VerdictChange(
            key=key,
            title=new[key].title,
            before=old[key].verdict,
            after=new[key].verdict,
            fit_before=old[key].fit,
            fit_after=new[key].fit,
        )
        for key in old.keys() & new.keys()
        if old[key].verdict != new[key].verdict or old[key].fit != new[key].fit
    ]
    return Comparison(
        blockers=[],
        notes=notes,
        entered=[new[key] for key in new.keys() - old.keys()],
        left=[old[key] for key in old.keys() - new.keys()],
        changed=sorted(changed, key=lambda c: -abs(c.fit_after - c.fit_before)),
        unchanged=len(old.keys() & new.keys()) - len(changed),
    )
