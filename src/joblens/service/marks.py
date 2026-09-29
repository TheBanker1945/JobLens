"""Marking a vacancy, with a reason (7.7 step 3): the person's side of a match.

A mark is the record the viewer (4.4) writes and every eval reads: a
`Decision` appended to the CV's `CVLabels`, never overwriting the one before
-- changing your mind is data, not a correction. The reason is required and
kept as typed; nothing here summarises it or turns a pattern of marks into a
rule (CLAUDE.md).

What the judge said and where retrieval put the vacancy are read off the run,
not sent by the page, so a mark cannot claim to be about a state that was
never on screen. And the vacancy is looked up in the run rather than in the
open corpus, so one that closed since the match can still be marked.
"""

from joblens.evals.matching import Call, CVLabels, Decision
from joblens.service.errors import ServiceError
from joblens.storage import Store

# Long enough for a paragraph, short enough that nobody stores a document here.
REASON_LIMIT = 1000


class NotInRun(ServiceError):
    """No such match for this person, or no such vacancy in it."""


class MarkRefused(ServiceError):
    """A mark without a reason, or with one too long to be a reason."""


def mark(
    store: Store,
    run_id: str,
    *,
    key: str,
    call: Call,
    reason: str,
    judged_by: str,
) -> Decision:
    """Record what this person thinks of one vacancy in one of their runs."""
    try:
        record = store.load_run(run_id)
    except KeyError as err:
        raise NotInRun("There is no such match.") from err
    judged = record.by_key().get(key)
    ranked = record.ranked_by_key().get(key)
    if judged is None and ranked is None:
        raise NotInRun("That vacancy is not in this match.")
    reason = reason.strip()
    if not reason:
        raise MarkRefused("A mark needs a reason: say why, in your own words.")
    if len(reason) > REASON_LIMIT:
        raise MarkRefused(f"Keep the reason under {REASON_LIMIT} characters.")

    cv = record.stamp.cv_name
    labels = store.load_labels(cv) or CVLabels(
        cv=cv, corpus=record.stamp.corpus, judged_by=judged_by
    )
    decision = Decision(
        key=key,
        call=call,
        reason=reason,
        run=run_id,
        verdict=judged.verdict.value if judged else "",
        fit=judged.fit if judged else None,
        rank=ranked.rank if ranked else None,
    )
    labels.record(decision)
    store.save_labels(labels)
    return decision
