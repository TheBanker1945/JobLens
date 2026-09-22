"""Is the judge honest, and does it agree with a human about who should apply?

    uv run python scripts/eval_judge.py
    uv run python scripts/eval_judge.py --top 15 --cv sanne_vermeulen
    uv run python scripts/eval_judge.py --fresh      # ignore stored answers

Three measurements, and only one of them needs an opinion:

**Faithfulness** needs none. Every quote the judge produces is looked for in the
text it says it came from; the share that is really there is the number. Anything
below 100% is a claim someone would have read and believed.

**Agreement** uses the labels from milestone 3.5 (evals/cv-matches/). The verdict
is compared with the judgement of the person who labelled that CV, and the two
mistakes are counted separately, because they are not equally bad: calling a
vacancy `strong` when the label says "would not apply" wastes an application,
while calling one `weak` that the label says to apply to only costs a line in a
list.

**The control CV** needs no labels either. Ingrid Solheim fits nothing in this
corpus, so every `strong` she is given is a false promise, and this is where the
answer to "what should it do when nothing fits" has to come from.

**The refusal** (3.7) is then checked against all four: the rule in cv/outcome.py
must refuse the control and refuse nobody else. It is one rule over four CVs, so
it is a check that it is not obviously wrong rather than a measurement of how
often it is right.

Answers are stored per (CV, vacancy, prompt version) so a rerun costs nothing.
What is stored is the model's raw answer, and the quote check is re-run on load:
a change to `verify` is measured against the answers already paid for.
"""

import argparse
import sys
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import load_corpus
from joblens.cv.judge import (
    PROMPT_VERSION,
    Judged,
    MatchJudgement,
    Verdict,
    judge_matches,
    verify,
)
from joblens.cv.match import prepare_cv, queries_for, search_with_cv
from joblens.cv.outcome import Fit, assess
from joblens.cv.read import CV_DIRECTORIES, find_cv
from joblens.cv.store import CVCache
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.store import CachedEmbedder, cache_path
from joblens.evals.matching import CVLabels
from joblens.llm.client import LLMClient
from joblens.llm.pricing import cost_usd, format_cost
from joblens.llm.structured import default_mode
from joblens.storage import FileStore

ROOT = Path(__file__).parent.parent
SAMPLE_CVS = ROOT / "data" / "samples" / "cvs"
CACHE_DIR = ROOT / "data" / "cache"

LABEL_NAMES = {2: "would apply", 1: "might", 0: "would not"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default="raw")
    parser.add_argument("--top", type=int, default=10, help="shortlist size per CV")
    parser.add_argument("--cv", help="only this CV")
    parser.add_argument("--fresh", action="store_true", help="ignore stored answers")
    parser.add_argument(
        "--cv-dir", type=Path, help="look here first (default: samples, then raw)"
    )
    parser.add_argument(
        "--strip-name",
        metavar="NAME",
        help="remove this name, as the labelling run did: the CV a variant reads "
        "has to be the CV that was judged, character for character",
    )
    args = parser.parse_args()

    load_dotenv()
    labels = [
        one for one in FileStore(ROOT).labels() if not args.cv or args.cv in one.cv
    ]
    if not labels:
        print("No judged CVs in evals/cv-matches or data/raw/cv-labels.")
        return 1

    corpus = load_corpus(args.corpus).extracted()
    cv_settings = load_llm_settings(prefix="CV")
    embed_settings = load_llm_settings(prefix="EMBED")
    cache = CVCache(CACHE_DIR / "cv-profiles.json")
    print(
        f"{len(labels)} CVs x top {args.top} of {len(corpus)} vacancies, "
        f"judged by {cv_settings.model}, prompt {PROMPT_VERSION}"
    )

    rows: list[tuple[CVLabels, list[Judged]]] = []
    tokens_in = tokens_out = 0
    try:
        with LLMClient(cv_settings) as client:
            for one in labels:
                judged, paid = judge_one(
                    one, corpus, client, cv_settings, embed_settings, cache, args
                )
                rows.append((one, judged))
                tokens_in += sum(j.prompt_tokens for j in judged)
                tokens_out += sum(j.output_tokens for j in judged)
                print(f"  {one.cv}: {len(judged)} judged ({paid} new)")
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1

    print_faithfulness(rows)
    print_agreement(rows)
    print_control(rows)
    print_refusal(rows, len(corpus))
    cost = cost_usd(cv_settings, tokens_in, tokens_out)
    print(f"\n{tokens_in} tokens in, {tokens_out} out  |  {format_cost(cost)} this run")
    return 0


def judge_one(
    labels: CVLabels, corpus, client, cv_settings, embed_settings, cache, args
) -> tuple[list[Judged], int]:
    """The shortlist for one CV, judged, with stored answers reused."""
    path = find_cv(labels.cv, _directories(args))
    if path is None:
        raise SystemExit(
            f"No CV file for {labels.cv!r}. Looked in "
            + ", ".join(str(d) for d in _directories(args))
        )
    prepared = prepare_cv(
        path,
        client,
        name=args.strip_name,
        model=cv_settings.model,
        cache=cache,
    )
    parts = queries_for(
        prepared, "raw", client=client, model=cv_settings.model, cache=cache
    )
    with EmbeddingClient(embed_settings) as embedder:
        index = VacancyIndex.build(
            corpus.vacancies,
            corpus.details,
            CachedEmbedder(embedder, cache_path(CACHE_DIR, embed_settings.model)),
        )
        matches = search_with_cv(index, parts, top_k=args.top)

    kind = f"judgement-{PROMPT_VERSION}"
    stored, todo = [], []
    for match in matches:
        key = _key(prepared.text, match)
        hit = None if args.fresh else cache.get(kind, cv_settings.model, key)
        if hit is None:
            todo.append(match)
        else:
            stored.append(_rebuild(hit, match, prepared.text))

    fresh, failures = judge_matches(
        prepared.text, todo, client, mode=default_mode(cv_settings)
    )
    for one in fresh:
        cache.put(
            kind,
            cv_settings.model,
            _key(prepared.text, one.match),
            (one.raw or one.judgement).model_dump(mode="json"),
        )
    for failure in failures:
        print(f"    failed: {failure}")
    return sorted(stored + fresh, key=lambda j: j.rank_key, reverse=True), len(fresh)


def _key(cv_text: str, match) -> str:
    return f"{match.vacancy.key}\0{cv_text}\0{match.vacancy.text}"


def _rebuild(payload: dict, match, cv_text: str) -> Judged:
    """A stored answer, checked again with today's `verify`."""
    raw = MatchJudgement.model_validate(payload)
    checked = verify(raw, cv_text, match.vacancy.text)
    return Judged(
        match=match,
        judgement=checked.judgement,
        dropped=checked.dropped,
        quotes=checked.quotes,
        raw=raw,
    )


def print_faithfulness(rows) -> None:
    print("\n===== quotes that are really in the source =====")
    print(f"{'cv':<18} {'quotes':>7} {'verified':>9} {'dropped':>8}")
    total = dropped = 0
    for labels, judged in rows:
        quotes = sum(one.quotes for one in judged)
        missing = sum(len(one.dropped) for one in judged)
        total, dropped = total + quotes, dropped + missing
        share = (quotes - missing) / quotes if quotes else 1.0
        print(f"{labels.cv:<18} {quotes:>7} {share:>8.0%} {missing:>8}")
    share = (total - dropped) / total if total else 1.0
    print(f"{'all':<18} {total:>7} {share:>8.0%} {dropped:>8}")
    for labels, judged in rows:
        for one in judged:
            for drop in one.dropped:
                print(f"  dropped [{drop.kind}] {labels.cv}: {drop.quote[:70]!r}")


def print_agreement(rows) -> None:
    """The verdict against the label, for the shortlisted vacancies that have one."""
    print("\n===== verdict against the label =====")
    print(
        f"{'cv':<18} {'verdict':<10} "
        + "  ".join(f"{n:>11}" for n in LABEL_NAMES.values())
    )
    costly = cheap = 0
    for labels, judged in rows:
        if not labels.relevant and not labels.maybe:
            continue
        counts = {(v, g): 0 for v in Verdict for g in LABEL_NAMES}
        unlabelled = 0
        for one in judged:
            key = one.match.vacancy.key
            if key not in labels.judged:
                unlabelled += 1
                continue
            counts[(one.judgement.verdict, labels.grade(key))] += 1
        for verdict in Verdict:
            row = "  ".join(f"{counts[(verdict, g)]:>11}" for g in LABEL_NAMES)
            print(f"{labels.cv:<18} {verdict:<10} {row}")
        costly += counts[(Verdict.STRONG, 0)]
        cheap += counts[(Verdict.WEAK, 2)]
        if unlabelled:
            print(f"{'':<18} ({unlabelled} shortlisted vacancies were never labelled)")
    print(
        f"\n  strong on a 'would not apply': {costly}   "
        f"(wastes an application -- the expensive mistake)"
    )
    print(
        f"  weak on a 'would apply':       {cheap}   "
        f"(costs a line in a list -- the cheap one)"
    )
    print_disagreements(rows)


def print_disagreements(rows) -> None:
    """Every disagreement, with the sentence the person wrote next to it.

    The whole of phase 3 started here: five disagreements on one CV, and no way
    to tell whether the judge or the person was wrong, because the label was a
    key in a list. A reason is not evidence that the judge is wrong -- it is the
    thing that makes the question answerable at all. Reasons are printed exactly
    as they were typed; nothing here groups them or infers a rule from them.
    """
    printed = False
    for labels, judged in rows:
        for one in judged:
            key = one.match.vacancy.key
            if key not in labels.judged:
                continue
            grade, verdict = labels.grade(key), one.judgement.verdict
            disagrees = (verdict is Verdict.STRONG and grade == 0) or (
                verdict is Verdict.WEAK and grade == 2
            )
            if not disagrees:
                continue
            if not printed:
                print("\n===== where the judge and the person disagree =====")
                printed = True
            decision = labels.decision_for(key)
            said = {2: "would apply", 1: "might", 0: "would not apply"}[grade]
            print(
                f"\n  {labels.cv}: judge {verdict} {one.judgement.fit}, "
                f"you {said}\n    {one.match.vacancy.title}"
            )
            print(
                f'    your reason: "{decision.reason}"'
                if decision and decision.reason
                else "    your reason: none recorded (labelled before 4.4)"
            )
    if printed:
        print(
            "\n  A reason is the person's own sentence, printed as typed. Whether "
            "the\n  judge or the label is wrong is a question for whoever reads "
            "these two\n  lines -- this eval only makes sure they are on the "
            "same screen."
        )


def print_control(rows) -> None:
    for labels, judged in rows:
        if labels.relevant or labels.maybe:
            continue
        counts = {verdict: 0 for verdict in Verdict}
        for one in judged:
            counts[one.judgement.verdict] += 1
        best = max((one.judgement.fit for one in judged), default=0)
        print(f"\n===== {labels.cv}: the control, nothing here fits =====")
        print("  " + "   ".join(f"{counts[v]} {v}" for v in Verdict))
        print(f"  highest fit given: {best}")
        print(
            "  every 'strong' here is a false promise; a corpus with no right "
            "answer should produce none."
        )


def print_refusal(rows, corpus: int) -> None:
    """Does "nothing here fits you" fire on the CV it should, and only there?

    A refusal cannot be scored against the labels -- a label says whether to
    apply to one vacancy, not whether a whole run should have been refused. What
    can be checked is the one thing that would make it useless in either
    direction: refusing somebody who has matches, or reassuring the control.
    """
    print("\n===== the refusal =====")
    print(f"{'cv':<18} {'strong':>6} {'possible':>9} {'weak':>5}  {'outcome':<13} ok?")
    wrong = 0
    for labels, judged in rows:
        outcome = assess(judged, corpus=corpus, corpus_name="raw")
        counts = outcome.counts
        has_matches = bool(labels.relevant or labels.maybe)
        # The control should be refused; a CV with labelled matches should not.
        correct = outcome.refused is not has_matches
        wrong += not correct
        print(
            f"{labels.cv:<18} {counts['strong']:>6} {counts['possible']:>9} "
            f"{counts['weak']:>5}  {outcome.fit.value:<13} {'yes' if correct else 'NO'}"
        )
    print(
        f"\n  {wrong} CV(s) got the wrong answer. The rule is two counts and no "
        f"threshold:\n  no strong and no possible is a refusal, no strong alone "
        f"is 'nothing is a clear fit'.\n  3.5 measured that the cosine cannot do "
        f"this: 0.12 of separation on one control CV."
    )
    middle = [
        labels.cv
        for labels, judged in rows
        if assess(judged, corpus=corpus).fit is Fit.NO_CLEAR_FIT
    ]
    if middle:
        print(
            "  the middle band caught: "
            + ", ".join(middle)
            + " -- a one-band rule would have refused them."
        )


def _directories(args) -> tuple[Path, ...]:
    """Where to look for a CV: an explicit --cv-dir first, then the usual two."""
    return (args.cv_dir, *CV_DIRECTORIES) if args.cv_dir else CV_DIRECTORIES


if __name__ == "__main__":
    sys.exit(main())
