"""What the pages are sent: shapes built from the store for one screen each.

A route answers with a model from here when the screen needs more than one
stored thing at once. The dashboard is the first (7.7): one request gives it
the person, their CV, what they want, their AI and budget, and the best of
their latest match -- rather than seven requests and a page that fills in
piece by piece.
"""

from collections import Counter
from datetime import datetime

from pydantic import BaseModel

from joblens.corpus import Corpus
from joblens.cv.runs import JudgedRow, RunRecord
from joblens.cv.schema import CVProfile
from joblens.extraction.schema import VacancyDetails
from joblens.preferences import Preferences
from joblens.service import Models, ai, budget
from joblens.storage import CVRecord, Database, Job, PostgresStore, User
from joblens.vault import Vault

# How many of the latest match's recommended vacancies the dashboard shows;
# the matches page (7.7 step 3) shows them all.
ON_DASHBOARD = 3
RECOMMENDED = ("strong", "possible")


class CVSummary(BaseModel):
    """A CV in a list: enough to choose one, without its text."""

    id: str
    name: str
    filename: str
    uploaded_at: datetime
    active: bool
    removed: dict[str, int]  # what redaction took out, counted
    characters: int
    has_profile: bool

    @classmethod
    def of(cls, cv: CVRecord) -> "CVSummary":
        return cls(
            **cv.model_dump(include=set(cls.model_fields) & set(CVRecord.model_fields)),
            characters=len(cv.text),
            has_profile=cv.profile is not None,
        )


class CVDetail(CVSummary):
    """One CV, with exactly the text that is sent to a model."""

    text: str
    profile: CVProfile | None

    @classmethod
    def of(cls, cv: CVRecord) -> "CVDetail":
        return cls(**CVSummary.of(cv).model_dump(), text=cv.text, profile=cv.profile)


class MatchCard(BaseModel):
    """One judged vacancy, as a card: the verdict, one quote, one gap."""

    key: str
    title: str
    company: str | None
    city: str | None
    url: str
    work_mode: str | None
    hours_min: int | None
    hours_max: int | None
    verdict: str
    fit: int
    quote: str | None  # the first line of the CV that answers it (checked)
    gap: str | None  # the first thing it asks that the CV does not show
    gap_required: bool | None


class LatestRun(BaseModel):
    id: str
    at: datetime
    corpus_size: int  # how many open vacancies were compared
    strong: int
    possible: int
    weak: int
    moved: int  # moved back by the person's preferences
    recommended: list[MatchCard]  # the best few, strong or possible


class Dashboard(BaseModel):
    user: User
    cv: CVSummary | None
    preferences: Preferences
    ai: ai.AIChoice
    usage: budget.Usage
    latest: LatestRun | None  # the newest match of the active CV
    open_match: Job | None  # a match still running, to follow


def dashboard(
    user: User,
    store: PostgresStore,
    *,
    corpus: Corpus,
    models: Models,
    database: Database,
    vault: Vault | None,
    budgets: budget.Budgets,
) -> Dashboard:
    cv = store.active_cv()
    paid_by = "own" if store.provider_key() and vault else "operator"
    return Dashboard(
        user=user,
        cv=CVSummary.of(cv) if cv else None,
        preferences=Preferences.model_validate(store.load_preferences() or {}),
        ai=ai.choice(store, models),
        usage=budget.usage(database, user, paid_by, budgets),
        latest=latest_run(store, cv, corpus) if cv else None,
        open_match=next((job for job in store.jobs(limit=5) if job.open), None),
    )


def latest_run(store: PostgresStore, cv: CVRecord, corpus: Corpus) -> LatestRun | None:
    """The newest run of this CV. An account can hold runs of other CVs (an
    import brings a laptop's runs); only this one's matches belong here."""
    newest = next((one for one in store.runs() if one.cv == cv.name), None)
    if newest is None:
        return None
    record = store.load_run(newest.id)
    return summarise(newest.id, record, corpus)


def summarise(run_id: str, record: RunRecord, corpus: Corpus) -> LatestRun:
    verdicts = Counter(str(row.verdict) for row in record.rows)
    cards = [
        card(row, corpus.details.get(row.key))
        for row in record.rows
        if str(row.verdict) in RECOMMENDED
    ]
    return LatestRun(
        id=run_id,
        at=record.stamp.at,
        corpus_size=record.stamp.corpus_size,
        strong=verdicts["strong"],
        possible=verdicts["possible"],
        weak=verdicts["weak"],
        moved=sum(1 for row in record.ranking if row.conflicts),
        recommended=cards[:ON_DASHBOARD],
    )


def card(row: JudgedRow, details: VacancyDetails | None) -> MatchCard:
    # A required gap says more than a nice-to-have, so it is shown first.
    gap = next((one for one in row.gaps if one.required), None) or next(
        iter(row.gaps), None
    )
    return MatchCard(
        key=row.key,
        title=row.title,
        company=row.company or (details.company if details else None),
        city=row.city or (details.city if details else None),
        url=row.url,
        work_mode=details.work_mode.value if details and details.work_mode else None,
        hours_min=details.hours_min if details else None,
        hours_max=details.hours_max if details else None,
        verdict=str(row.verdict),
        fit=row.fit,
        quote=row.claims[0].quote if row.claims else None,
        gap=gap.requirement if gap else None,
        gap_required=gap.required if gap else None,
    )
