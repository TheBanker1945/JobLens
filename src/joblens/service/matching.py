"""Matching a CV against the vacancies, as a call rather than a script.

Until 7.1 the whole sequence -- read the CV, rank the corpus, judge the head of
it, add it up, store it -- lived in scripts/match_cv.py's `main()`, in between
the prints. A web request cannot call a `main()`: it cannot hand it an upload,
it cannot read its progress, and the only way it could learn what went wrong
was to parse what was printed. This module is that sequence with the prints
taken out. The script is now one caller of it; a web request is the next.

**Two stages, because they cost different things.** `rank` reads the CV and
puts every vacancy in order: one profile and one wishlist advert, both cached,
then arithmetic over vectors that are already stored -- free the second time,
and a few seconds. `judge` sends the shortlist to a model: about 0.36 cent and
two seconds a vacancy, 20 to 60 seconds a run. A page can show the ranking at
once and fill in the verdicts as they arrive, and a free tier can give the
first away and meter the second.

**Settings in, never read in here.** Which model reads the CV and which one
embeds it arrive as `Models`; nothing in this module reads .env. That is all
"bring your own AI" asks of this layer: the API builds `Models` from a user's
account instead of from the environment, and everything below is unchanged.

**Clients are opened here, from a factory the caller can swap.** The defaults
are the real clients. A test passes fakes and never touches the network; a
server that one day keeps connections open between requests passes a factory
that hands out the open one.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
import openai

from joblens.config import LLMSettings
from joblens.corpus import Corpus
from joblens.cv.gaps import GapSummary, summarise_gaps
from joblens.cv.judge import PROMPT_VERSION, Judged, judge_matches
from joblens.cv.match import (
    DEFAULT_STYLE,
    PER_EMPLOYER,
    CVMatch,
    PreparedCV,
    Shortlist,
    prepare_cv,
    rank_cv,
    shortlist,
    styles_of,
)
from joblens.cv.outcome import Outcome, assess
from joblens.cv.read import CVFile, UnreadableCVError
from joblens.cv.requirements import (
    REQUIREMENTS_VERSION,
    RequirementBook,
    judge_by_requirements,
)
from joblens.cv.runs import RunRecord, RunStamp, build_record, corpus_digest, digest
from joblens.cv.store import CVCache
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.store import CachedEmbedder, cache_path
from joblens.llm.client import LLMClient
from joblens.llm.pricing import cost_usd
from joblens.llm.structured import StructuredError, default_mode
from joblens.llm.types import ChatClient
from joblens.preferences import Conflict, Preferences, for_judge, rerank
from joblens.service.errors import CVUnreadable, ProviderRefused, ProviderUnreachable
from joblens.storage import Store

JudgeKind = Literal["holistic", "requirements"]
JUDGES: tuple[JudgeKind, ...] = ("holistic", "requirements")

# The two caches a match reads and writes, inside the cache directory. The
# embeddings sit beside them, one SQLite file per model (embeddings/store.py).
PROFILES = "cv-profiles.json"  # what a model made of a CV, and its wishlist advert
REQUIREMENTS = "requirements.json"  # a vacancy's requirements, read once (6.3)


@dataclass(frozen=True)
class Models:
    """Which model does what in a match."""

    cv: LLMSettings  # reads the CV, writes its wishlist advert, judges
    # Has to be the model the corpus was embedded with: a CV and a vacancy are
    # only comparable in one vector space. So this one is the operator's to
    # choose, even when `cv` is a user's own key.
    embed: LLMSettings


@dataclass(frozen=True)
class MatchRequest:
    """What a person asks for. The same knobs as match_cv.py's flags."""

    cv: Path | CVFile
    strip_name: str | None = None  # the name to remove, exactly as the CV has it
    style: str = DEFAULT_STYLE
    top: int = 10  # how many to judge
    per_employer: int = PER_EMPLOYER  # 0 for no cap
    judge: JudgeKind = "holistic"
    # What the person wants (7.3): moves vacancies before the shortlist is cut,
    # and tells the holistic judge the rules that need reading.
    preferences: Preferences | None = None

    def __post_init__(self) -> None:
        # Checked here and not only in argparse: a request from a browser gets
        # the same answer, before anything is read or paid for. A negative
        # `top` would never reach the cut and judge the whole corpus.
        styles_of(self.style)
        if self.judge not in JUDGES:
            raise ValueError(f"unknown judge {self.judge!r}: use one of {JUDGES}")
        if self.top < 0 or self.per_employer < 0:
            raise ValueError("top and per_employer cannot be negative")


@dataclass(frozen=True)
class Progress:
    """Where a match is. `done` and `total` count vacancies while judging."""

    stage: Literal["reading", "ranking", "judging"]
    done: int = 0
    total: int = 0


@dataclass(frozen=True)
class Ranked:
    """The first stage: the CV read, and every vacancy put in order."""

    request: MatchRequest
    prepared: PreparedCV
    ranking: list[CVMatch]  # every vacancy in the index, best first
    chosen: Shortlist  # its head, at most `per_employer` each: what `judge` reads
    indexed: int  # how many vacancies could be ranked at all
    # With preferences: retrieval's own position of every vacancy, and what
    # each moved one contradicts. None and empty without.
    before: dict[str, int] | None = None
    conflicts: dict[str, list[Conflict]] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchRun:
    """The second stage: the shortlist judged, added up, and stored."""

    ranked: Ranked
    judged: list[Judged]  # best first: verdict, then fit
    failures: list[str]  # one line per vacancy that could not be judged
    outcome: Outcome  # whether anything here fits at all
    summary: GapSummary  # what keeps coming up that the CV does not show
    record: RunRecord  # what is stored, and what the viewer reads
    prompt_tokens: int
    output_tokens: int
    cost_usd: float | None  # None when the model has no known price
    run_id: str | None = None  # None when no store was given


OnProgress = Callable[[Progress], None]
ChatFactory = Callable[[LLMSettings], ChatClient]
EmbedFactory = Callable[[LLMSettings], EmbeddingClient]


def rank(
    request: MatchRequest,
    corpus: Corpus,
    models: Models,
    *,
    cache_dir: Path,
    progress: OnProgress | None = None,
    chat: ChatFactory = LLMClient,
    embed: EmbedFactory = EmbeddingClient,
) -> Ranked:
    """Read the CV and rank every vacancy in `corpus` against it.

    `corpus` is loaded by the caller: a script loads it once per run, a server
    once per start, and neither should pay for the other's choice.
    """
    report = progress or _silent
    cache = CVCache(cache_dir / PROFILES)
    with _translated(), ExitStack() as stack:
        client = stack.enter_context(closing(chat(models.cv)))
        report(Progress("reading"))
        prepared = prepare_cv(
            request.cv,
            client,
            name=request.strip_name,
            model=models.cv.model,
            mode=default_mode(models.cv),
            cache=cache,
        )
        report(Progress("ranking"))
        embedder = stack.enter_context(closing(embed(models.embed)))
        vectors = stack.enter_context(
            closing(CachedEmbedder(embedder, cache_path(cache_dir, models.embed.model)))
        )
        index = VacancyIndex.build(corpus.vacancies, corpus.details, vectors)
        # The whole corpus, not the shortlist: judging reads the head of this
        # list and the rest is stored, so a rejection by retrieval has a
        # position and a score you can go and look at (4.1).
        ranking = rank_cv(
            index,
            prepared,
            request.style,
            client=client,
            model=models.cv.model,
            cache=cache,
        )
        before, conflicts = None, {}
        if request.preferences and not request.preferences.is_empty():
            # Before the cut, so the shortlist is made of what the person
            # wants; nothing is removed, and each move is kept with its reason.
            moved = rerank(ranking, corpus.details, request.preferences)
            ranking, before, conflicts = moved.ranking, moved.before, moved.conflicts
        chosen = shortlist(
            ranking,
            request.top,
            per_employer=request.per_employer,
            details=corpus.details,
        )
    return Ranked(
        request,
        prepared,
        ranking,
        chosen,
        indexed=len(index),
        before=before,
        conflicts=conflicts,
    )


def judge(
    ranked: Ranked,
    corpus: Corpus,
    models: Models,
    *,
    cache_dir: Path,
    store: Store | None = None,
    progress: OnProgress | None = None,
    chat: ChatFactory = LLMClient,
) -> MatchRun:
    """Judge the shortlist, add the run up, and store it if given a store.

    One vacancy that cannot be judged is a line in `failures`, not an error:
    nine judged vacancies are still worth reading.
    """
    report = progress or _silent
    request, prepared = ranked.request, ranked.prepared
    matches = ranked.chosen.matches
    with _translated(), closing(chat(models.cv)) as client:
        report(Progress("judging", 0, len(matches)))
        judged, failures = judge_matches(
            prepared.text,
            matches,
            client,
            mode=default_mode(models.cv),
            judge=_requirement_judge(client, models.cv, cache_dir)
            if request.judge == "requirements"
            else None,
            on_done=lambda done, total, _match: report(
                Progress("judging", done, total)
            ),
            told=for_judge(request.preferences),
        )

    outcome = assess(judged, corpus=len(corpus), corpus_name=corpus.name)
    summary = summarise_gaps(
        judged, prepared.profile, corpus.details, cv_text=prepared.text
    )
    prompt_tokens = sum(one.prompt_tokens for one in judged)
    output_tokens = sum(one.output_tokens for one in judged)
    cost = cost_usd(models.cv, prompt_tokens, output_tokens)
    stamp = RunStamp(
        cv_name=prepared.name,
        cv_digest=digest(prepared.text),
        corpus=corpus.name,
        corpus_size=len(corpus),
        corpus_digest=corpus_digest(corpus.vacancies),
        embed_model=models.embed.model,
        judge_model=models.cv.model,
        cv_style=request.style,
        prompt_version=judge_version(request.judge),
        top=request.top,
        per_employer=request.per_employer,
        preferences=request.preferences.stamp() if request.preferences else "",
    )
    record = build_record(
        stamp,
        judged,
        outcome,
        summary,
        ranking=ranked.ranking,
        sent={match.vacancy.key for match in matches},
        capped={match.vacancy.key for match in ranked.chosen.capped},
        funnel=corpus.funnel,
        failures=failures,
        cost_usd=cost,
        before=ranked.before,
        conflicts=ranked.conflicts,
    )
    # Every write of a run goes through the store (4.2), which also decides
    # that two runs in the same minute are two runs and not one overwritten.
    run_id = store.save_run(record) if store is not None else None
    return MatchRun(
        ranked=ranked,
        judged=judged,
        failures=failures,
        outcome=outcome,
        summary=summary,
        record=record,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        run_id=run_id,
    )


def judge_version(kind: JudgeKind) -> str:
    """What a run's judgements are comparable with. compare_runs.py refuses two
    runs whose versions differ, and two judges are two scales."""
    if kind == "requirements":
        return f"requirements {REQUIREMENTS_VERSION}"
    return PROMPT_VERSION


def _requirement_judge(client: ChatClient, settings: LLMSettings, cache_dir: Path):
    """The 6.3 judge, with every vacancy's requirements kept between runs."""
    mode = default_mode(settings)
    book = RequirementBook(
        CVCache(cache_dir / REQUIREMENTS), client, model=settings.model, mode=mode
    )

    def one(cv_text: str, match: CVMatch) -> Judged:
        return judge_by_requirements(cv_text, match, client, book=book, mode=mode)

    return one


@contextmanager
def _translated() -> Iterator[None]:
    """The reader's, the SDK's and httpx's exceptions, as ours (errors.py)."""
    try:
        yield
    except UnreadableCVError as err:
        raise CVUnreadable(str(err)) from err
    except StructuredError as err:
        raise CVUnreadable(f"Could not read this CV into a profile:\n{err}") from err
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        raise ProviderUnreachable(f"Cannot reach a server: {err}") from err
    except openai.APIStatusError as err:
        # Reached and refused: a 503 "high demand" from Gemini ended seven of
        # ten runs on 2026-09-24 as a traceback, before a single line of
        # output. The SDK has already retried it. One judge call failing this
        # way is collected as that vacancy's failure; this is reading the CV or
        # embedding the query, without which there is no run at all.
        advice = (
            "503 and 429 are the provider being busy: try again in a few minutes."
            if err.status_code in (429, 503)
            else f"{err.message[:300]}"
        )
        raise ProviderRefused(
            f"The model provider refused the request ({err.status_code}), after "
            f"retrying. Nothing was judged and nothing was stored.\n{advice}",
            err.status_code,
        ) from err


def _silent(progress: Progress) -> None:
    pass
