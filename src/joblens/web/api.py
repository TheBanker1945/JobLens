"""What the viewer can ask for, as plain functions over a store and a corpus.

No HTTP in this file on purpose. `server.py` is the only place that knows about
requests, status codes and query strings, and it is thirty lines of routing
around these functions -- so the day this is hosted, the thing that gets replaced
is the transport and not the application. That was the brief's "treat the API
layer as the thing hosting will wrap later".

**The three buckets are the whole point of the milestone.** A run's report used
to show what it recommended. What it could not show was what it rejected, and
there are two kinds of that: the vacancies a model read and turned down (they
have a reason attached, and the reason can be wrong), and the vacancies retrieval
never showed to anybody (they have only a position and a score, and until 4.1
they did not even have those). They are separated here rather than in the page,
because the difference between them is a fact about the run and not a choice of
layout.
"""

from collections import Counter

from joblens.corpus import Corpus
from joblens.cv.judge import Verdict
from joblens.cv.outcome import Fit, Outcome
from joblens.cv.runs import JudgedRow, RankedRow, RunRecord
from joblens.evals.matching import Call, CVLabels, Decision
from joblens.extraction.schema import VacancyDetails
from joblens.sources.base import Vacancy
from joblens.storage import Store

# Above this, a verdict is something to look at; at or below it, the model is
# saying no. The bands are the judge's (cv/judge.py), not a new threshold.
RECOMMENDED = (Verdict.STRONG, Verdict.POSSIBLE)


def runs_view(store: Store) -> dict:
    return {"runs": [one.model_dump(mode="json") for one in store.runs()]}


def run_view(store: Store, corpus: Corpus, run_id: str) -> dict:
    """One run, split into what it recommended, what it read and turned down,
    and what it never showed anybody."""
    record = store.load_run(run_id)
    ranked = record.ranked_by_key()
    judged = record.by_key()

    recommended, rejected = [], []
    for row in record.rows:
        bucket = recommended if row.verdict in RECOMMENDED else rejected
        bucket.append(_judged_row(row, ranked.get(row.key)))

    never = [
        _ranked_row(row, corpus)
        for row in record.ranking
        if not row.judged and row.key not in judged
    ]
    boundary = record.boundary()
    return {
        "id": run_id,
        "stamp": record.stamp.model_dump(mode="json"),
        "stamp_line": record.stamp.line(),
        "funnel": record.funnel.model_dump(mode="json"),
        "funnel_line": record.funnel.line(),
        "outcome": _outcome(record),
        "cost_usd": record.cost_usd,
        "seconds": record.seconds,
        "prompt_tokens": record.prompt_tokens,
        "output_tokens": record.output_tokens,
        "failures": record.failures,
        "counts": {
            "recommended": len(recommended),
            "rejected": len(rejected),
            "never_shortlisted": len(never),
            "ranked": len(record.ranking),
        },
        "boundary": _boundary(boundary),
        "recommended": recommended,
        "rejected": rejected,
        "never_shortlisted": never,
        "gaps": {
            "groups": [group.model_dump(mode="json") for group in record.groups],
            "ungrouped": record.ungrouped,
            "already_on_cv": record.already_on_cv,
        },
    }


def labels_view(store: Store, run_id: str) -> dict:
    """What this person has already said about the vacancies in this run."""
    record = store.load_run(run_id)
    labels = store.load_labels(record.stamp.cv_name)
    return _labels_payload(record.stamp.cv_name, labels)


def record_decision(store: Store, corpus: Corpus, run_id: str, body: dict) -> dict:
    """Mark a vacancy, with a reason, against the run that is on screen.

    The reason is required and stored verbatim. Everything else -- which CV this
    is, what the judge had said, where retrieval had put it -- is read off the
    run rather than posted, so a decision cannot claim to be about a state that
    was never on screen.
    """
    record = store.load_run(run_id)
    cv = record.stamp.cv_name
    key = str(body.get("key") or "").strip()
    reason = str(body.get("reason") or "").strip()
    if not key:
        raise ValueError("which vacancy? pass a key")
    if key not in corpus.by_key():
        raise ValueError(f"no vacancy {key!r} in the {corpus.name} corpus")
    if not reason:
        raise ValueError(
            "a reason is required. 'weak but I would apply' is not a label until "
            "it says why -- that is what 4.4 exists to fix"
        )
    try:
        call = Call(str(body.get("call")))
    except ValueError as err:
        raise ValueError(
            f"call must be one of {', '.join(one.value for one in Call)}"
        ) from err

    labels = store.load_labels(cv)
    if labels is None:
        judged_by = str(body.get("judged_by") or "").strip()
        if not judged_by:
            raise ValueError(
                f"nobody has labelled {cv} yet, so these labels need a name on "
                "them: start the viewer with --judged-by 'your name'"
            )
        labels = CVLabels(cv=cv, corpus=record.stamp.corpus, judged_by=judged_by)

    judged_row = record.by_key().get(key)
    ranked_row = record.ranked_by_key().get(key)
    labels.record(
        Decision(
            key=key,
            call=call,
            reason=reason,
            run=run_id,
            verdict=judged_row.verdict.value if judged_row else "",
            fit=judged_row.fit if judged_row else None,
            rank=ranked_row.rank if ranked_row else None,
        )
    )
    store.save_labels(labels)
    return _labels_payload(cv, labels)


def _labels_payload(cv: str, labels: CVLabels | None) -> dict:
    if labels is None:
        return {"cv": cv, "judged_by": "", "decisions": {}, "counts": {}}
    decisions = {
        key: one.model_dump(mode="json") for key, one in labels.reasons().items()
    }
    return {
        "cv": cv,
        "judged_by": labels.judged_by,
        "decisions": decisions,
        "counts": {
            "apply": len(labels.relevant),
            "maybe": len(labels.maybe),
            "judged": len(labels.judged),
            "with_a_reason": len(decisions),
        },
    }


def vacancy_view(
    store: Store, corpus: Corpus, key: str, run_id: str | None = None
) -> dict:
    """One vacancy: what the board wrote, what we extracted, what a run made of it.

    The brief asked for the vacancy text and the extracted fields one click away,
    and this is where a rejection is actually checked: the judge said the advert
    demands four years, and here is the advert.
    """
    vacancy = corpus.by_key().get(key)
    if vacancy is None:
        raise KeyError(f"no vacancy {key!r} in the {corpus.name} corpus")
    found: dict = {
        "vacancy": _vacancy(vacancy),
        "text": vacancy.text,
        "details": _details(corpus.details.get(key)),
    }
    if run_id:
        record = store.load_run(run_id)
        row = record.by_key().get(key)
        found["judgement"] = (
            _judged_row(row, record.ranked_by_key().get(key)) if row else None
        )
        found["ranked"] = _rank_only(record.ranked_by_key().get(key))
    return found


def corpus_view(corpus: Corpus, query: str = "", limit: int = 100) -> dict:
    """The corpus itself, so it can be read without a run in the way.

    A search over the stored text and nothing cleverer: this is the raw material,
    and the point of looking at it is to see what the embedder and the judge were
    given, not to rank it again.
    """
    wanted = query.strip().lower()
    rows = []
    for vacancy in corpus.vacancies:
        if wanted and wanted not in _haystack(vacancy):
            continue
        rows.append(
            _vacancy(vacancy)
            | {
                "extracted": vacancy.key in corpus.details,
                "chars": len(vacancy.text),
            }
        )
    return {
        "corpus": corpus.name,
        "total": len(corpus.vacancies),
        "matched": len(rows),
        "shown": min(len(rows), limit),
        "vacancies": rows[:limit],
        "funnel": corpus.funnel.model_dump(mode="json"),
        "funnel_line": corpus.funnel.line(),
    }


def _haystack(vacancy: Vacancy) -> str:
    parts = (vacancy.title, vacancy.company or "", vacancy.city or "", vacancy.text)
    return " ".join(parts).lower()


def _outcome(record: RunRecord) -> dict:
    """The honest sentence about the run, from the code that already writes it.

    Rebuilt rather than re-derived in JavaScript: there is exactly one place that
    decides what "nothing here fits you" is allowed to claim, and the page should
    print what it says rather than a second opinion that drifts from it.
    """
    counts = Counter(row.verdict for row in record.rows)
    outcome = Outcome(
        fit=Fit(record.outcome),
        counts={verdict: counts.get(verdict, 0) for verdict in Verdict},
        best_fit=max((row.fit for row in record.rows), default=0),
        judged=len(record.rows),
        corpus=record.stamp.corpus_size,
        corpus_name=record.stamp.corpus,
    )
    return {
        "fit": outcome.fit.value,
        "refused": outcome.refused,
        "headline": outcome.headline(),
        "advice": outcome.advice(),
        "counts": {verdict.value: count for verdict, count in outcome.counts.items()},
    }


def _boundary(pair: tuple[RankedRow, RankedRow] | None) -> dict | None:
    if pair is None:
        return None
    last, first = pair
    return {
        "last_read": _rank_only(last),
        "first_unread": _rank_only(first),
        "gap": round(last.score - first.score, 4),
    }


def _judged_row(row: JudgedRow, ranked: RankedRow | None) -> dict:
    return {
        "key": row.key,
        "title": row.title,
        "company": row.company,
        "city": row.city,
        "url": row.url,
        "verdict": row.verdict.value,
        "fit": row.fit,
        "summary": row.summary,
        "score": row.score,
        "rank": ranked.rank if ranked else None,
        "part": ranked.part if ranked else "",
        "evidence": row.evidence,
        "dropped": row.dropped,
        "claims": [claim.model_dump(mode="json") for claim in row.claims],
        "gaps": [gap.model_dump(mode="json") for gap in row.gaps],
        "judged": True,
    }


def _ranked_row(row: RankedRow, corpus: Corpus) -> dict:
    return row.model_dump(mode="json") | {
        "extracted": row.key in corpus.details,
    }


def _rank_only(row: RankedRow | None) -> dict | None:
    if row is None:
        return None
    return {
        "rank": row.rank,
        "key": row.key,
        "title": row.title,
        "score": row.score,
        "part": row.part,
        "judged": row.judged,
    }


def _vacancy(vacancy: Vacancy) -> dict:
    return {
        "key": vacancy.key,
        "title": vacancy.title,
        "company": vacancy.company,
        "city": vacancy.city,
        "url": vacancy.url,
        "source": vacancy.source,
        "posted_at": vacancy.posted_at.isoformat() if vacancy.posted_at else None,
    }


def _details(details: VacancyDetails | None) -> dict | None:
    return details.model_dump(mode="json") if details else None
