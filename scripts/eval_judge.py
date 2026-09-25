"""Is the judge honest, and does it agree with a human about who should apply?

    uv run python scripts/eval_judge.py
    uv run python scripts/eval_judge.py --top 15 --cv sanne_vermeulen
    uv run python scripts/eval_judge.py --fresh      # ignore stored answers
    uv run python scripts/eval_judge.py --labelled   # every labelled vacancy

Four measurements, and only one of them needs an opinion:

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

**Order** (6.1) is what the judge is actually used for -- the list is sorted by
its verdict and fit -- and the three counts above cannot see it. On the real CV
the judge made no expensive mistake and still ordered the shortlist worse than
retrieval had. So every CV also gets its concordance (of every two vacancies
the person graded differently, the share the judge puts the right way round)
and the nDCG of the same vacancies in retrieval's order and in the judge's.
See `joblens/evals/judging.py`.

**Two ways to choose what is judged.** By default the shortlist `match_cv.py`
would judge: the product as it is used, which needs the index. `--labelled`
judges every vacancy the person labelled instead, retrieval or no retrieval:
more pairs (24 on the real CV against the ~5 labelled ones a shortlist holds),
no embedding call, and a number that does not move when the corpus grows under
it. Retrieval's order for those vacancies comes from the newest stored run of
that CV, and the screen names the run.

**The refusal** (3.7) is then checked against all four: the rule in cv/outcome.py
must refuse the control and refuse nobody else. It is one rule over four CVs, so
it is a check that it is not obviously wrong rather than a measurement of how
often it is right.

Answers are stored per (CV, vacancy, prompt version) so a rerun costs nothing,
and each one is stored as it arrives rather than when the run ends. What is
stored is the model's raw answer, and the quote check is re-run on load: a
change to `verify` is measured against the answers already paid for.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import load_corpus
from joblens.cv.documents import QueryPart
from joblens.cv.judge import (
    Judged,
    Verdict,
)
from joblens.cv.match import DEFAULT_STYLE, CVMatch, prepare_cv, rank_cv
from joblens.cv.outcome import Fit, assess
from joblens.cv.read import CV_DIRECTORIES, find_cv
from joblens.cv.store import CVCache
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.store import CachedEmbedder, cache_path
from joblens.evals.judging import JudgeVariant, concordance, reordered_ndcg
from joblens.evals.matching import CVLabels
from joblens.llm.client import LLMClient
from joblens.llm.pricing import cost_usd, format_cost
from joblens.llm.structured import default_mode
from joblens.preferences import Preferences, for_judge
from joblens.storage import FileStore, Store

ROOT = Path(__file__).parent.parent
SAMPLE_CVS = ROOT / "data" / "samples" / "cvs"
CACHE_DIR = ROOT / "data" / "cache"

LABEL_NAMES = {2: "would apply", 1: "might", 0: "would not"}


@dataclass
class CVRun:
    """One CV's judgements, and where retrieval had put the same vacancies."""

    labels: CVLabels
    judged: list[Judged]
    retrieval: dict[str, int] = field(default_factory=dict)  # key -> position
    retrieval_from: str = ""  # which ranking those positions come from
    missing: int = 0  # labelled vacancies no longer in the corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default="raw")
    parser.add_argument("--top", type=int, default=10, help="shortlist size per CV")
    parser.add_argument("--cv", help="only this CV")
    parser.add_argument("--fresh", action="store_true", help="ignore stored answers")
    parser.add_argument(
        "--labelled",
        action="store_true",
        help="judge every labelled vacancy instead of the retrieval shortlist",
    )
    parser.add_argument(
        "--judge",
        choices=["holistic", "requirements"],
        default="holistic",
        help="one verdict in one go (judge.py), or one answer per requirement "
        "added up in code (requirements.py, 6.3)",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.0, help="the judge's temperature"
    )
    parser.add_argument(
        "--thinking",
        choices=["on", "off"],
        help="override CV_THINKING for the judge (and name the answers after it)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=1,
        help="ask again as a separate stored answer: 2 is the second run of the "
        "same question, which is how run-to-run noise is measured",
    )
    parser.add_argument(
        "--style", default=DEFAULT_STYLE, help="how the CV asks, as in match_cv.py"
    )
    parser.add_argument(
        "--cv-dir", type=Path, help="look here first (default: samples, then raw)"
    )
    parser.add_argument(
        "--preferences",
        action="store_true",
        help="tell the holistic judge the rules stored by scripts/preferences.py "
        "(years, degree, sectors: 7.3). Stored apart from the answers without",
    )
    parser.add_argument(
        "--strip-name",
        metavar="NAME",
        help="remove this name, as the labelling run did: the CV a variant reads "
        "has to be the CV that was judged, character for character",
    )
    args = parser.parse_args()

    load_dotenv()
    store = FileStore(ROOT)
    labels = [one for one in store.labels() if not args.cv or args.cv in one.cv]
    if not labels:
        print("No judged CVs in evals/cv-matches or data/raw/cv-labels.")
        return 1

    corpus = load_corpus(args.corpus).extracted()
    cv_settings = load_llm_settings(prefix="CV")
    if args.thinking:
        cv_settings = cv_settings.model_copy(update={"thinking": args.thinking == "on"})
    told = None
    if args.preferences:
        stored = store.load_preferences()
        if stored is None:
            print("No preferences stored yet: scripts/preferences.py ask")
            return 1
        told = for_judge(Preferences.model_validate(stored))
        if told is None:
            print(
                "The stored preferences answer none of years, degree or sectors, "
                "so the judge would be told nothing: this would be the plain run."
            )
            return 1
    args.variant = JudgeVariant(
        args.temperature, cv_settings.thinking, args.sample, args.judge, told
    )
    embed_settings = load_llm_settings(prefix="EMBED")
    cache = CVCache(CACHE_DIR / "cv-profiles.json")
    chosen = "every labelled vacancy" if args.labelled else f"top {args.top}"
    print(
        f"{len(labels)} CVs x {chosen} of {len(corpus)} vacancies, "
        f"judged by {cv_settings.model}, {args.variant.describe()}"
    )

    rows: list[CVRun] = []
    tokens_in = tokens_out = 0
    try:
        # Five retries, not the default two: an eval runs a hundred calls into
        # whatever demand spike the provider is having (6.2 lost 14 of 124 to
        # "503 high demand"), and a failed call is only a hole in the table.
        with LLMClient(cv_settings, max_retries=5) as client:
            for one in labels:
                run, paid = judge_one(
                    one, corpus, client, cv_settings, embed_settings, cache, store, args
                )
                rows.append(run)
                tokens_in += sum(j.prompt_tokens for j in run.judged)
                tokens_out += sum(j.output_tokens for j in run.judged)
                gone = f", {run.missing} no longer in the corpus" if run.missing else ""
                print(f"  {one.cv}: {len(run.judged)} judged ({paid} new{gone})")
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1

    print_faithfulness(rows)
    print_agreement(rows)
    print_ordering(rows)
    print_control(rows)
    print_refusal(rows, len(corpus))
    cost = cost_usd(cv_settings, tokens_in, tokens_out)
    print(f"\n{tokens_in} tokens in, {tokens_out} out  |  {format_cost(cost)} this run")
    return 0


def judge_one(
    labels: CVLabels, corpus, client, cv_settings, embed_settings, cache, store, args
) -> tuple[CVRun, int]:
    """The vacancies to judge for one CV, judged, with stored answers reused."""
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
    if args.labelled:
        run, matches = labelled(labels, corpus, store)
    else:
        matches = shortlist(
            prepared, corpus, client, cv_settings, embed_settings, cache, args
        )
        run = CVRun(
            labels,
            [],
            {match.vacancy.key: position for position, match in enumerate(matches, 1)},
            "this shortlist",
        )

    judged, failures, paid = args.variant.judge(
        [(prepared.text, match) for match in matches],
        client,
        cache,
        model=cv_settings.model,
        mode=default_mode(cv_settings),
        fresh=args.fresh,
        requirements=CVCache(CACHE_DIR / "requirements.json"),
    )
    for failure in failures:
        print(f"    failed: {failure}")
    run.judged = judged
    return run, paid


def shortlist(prepared, corpus, client, cv_settings, embed_settings, cache, args):
    """The shortlist match_cv.py would judge, asked the same way."""
    with EmbeddingClient(embed_settings) as embedder:
        index = VacancyIndex.build(
            corpus.vacancies,
            corpus.details,
            CachedEmbedder(embedder, cache_path(CACHE_DIR, embed_settings.model)),
        )
        return rank_cv(
            index,
            prepared,
            args.style,
            client=client,
            model=cv_settings.model,
            cache=cache,
        )[: args.top]


def labelled(labels: CVLabels, corpus, store: Store) -> tuple[CVRun, list[CVMatch]]:
    """Every labelled vacancy still in the corpus, and retrieval's order for them.

    No index is built: the order comes from the newest stored run of this CV,
    which ranked the whole corpus (4.1). A CV with no stored run is judged all
    the same and simply has no retrieval column.
    """
    by_key = {vacancy.key: vacancy for vacancy in corpus.vacancies}
    run_id, ranked = _newest_ranking(labels.cv, store)
    part = QueryPart("labelled", "")
    # The run's own score breaks a tie between two equal judgements, as it does
    # in match_cv.py; a vacancy that run never ranked scores nothing.
    matches = [
        CVMatch(by_key[key], ranked[key].score if key in ranked else 0.0, "", part)
        for key in labels.judged
        if key in by_key
    ]
    run = CVRun(
        labels,
        [],
        {key: row.rank for key, row in ranked.items()},
        f"run {run_id}" if run_id else "",
        missing=len(labels.judged) - len(matches),
    )
    return run, matches


def _newest_ranking(cv: str, store: Store):
    for summary in store.runs():
        if summary.cv == cv and summary.ranked:
            return summary.id, store.load_run(summary.id).ranked_by_key()
    return "", {}


def print_faithfulness(rows) -> None:
    print("\n===== quotes that are really in the source =====")
    print(f"{'cv':<18} {'quotes':>7} {'verified':>9} {'dropped':>8}")
    total = dropped = 0
    for run in rows:
        labels, judged = run.labels, run.judged
        quotes = sum(one.quotes for one in judged)
        missing = sum(len(one.dropped) for one in judged)
        total, dropped = total + quotes, dropped + missing
        share = (quotes - missing) / quotes if quotes else 1.0
        print(f"{labels.cv:<18} {quotes:>7} {share:>8.0%} {missing:>8}")
    share = (total - dropped) / total if total else 1.0
    print(f"{'all':<18} {total:>7} {share:>8.0%} {dropped:>8}")
    for run in rows:
        for one in run.judged:
            for drop in one.dropped:
                print(f"  dropped [{drop.kind}] {run.labels.cv}: {drop.quote[:70]!r}")


def print_agreement(rows) -> None:
    """The verdict against the label, for the shortlisted vacancies that have one."""
    print("\n===== verdict against the label =====")
    print(
        f"{'cv':<18} {'verdict':<10} "
        + "  ".join(f"{n:>11}" for n in LABEL_NAMES.values())
    )
    costly = cheap = 0
    for run in rows:
        labels, judged = run.labels, run.judged
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
    for run in rows:
        labels = run.labels
        for one in run.judged:
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


def print_ordering(rows: list[CVRun]) -> None:
    """Does the judge's order beat the order retrieval gave the same vacancies?

    Only labelled vacancies count: an unlabelled one is not a "would not", and
    scoring it as one would punish whichever order found it. `pairs` is how
    many differently-graded pairs the concordance is made of, so a 0.80 over
    six pairs reads as the coin toss it nearly is.
    """
    print("\n===== order: does the judge beat retrieval? =====")
    print(
        f"{'cv':<18} {'labelled':>8} {'pairs':>6}  {'concordance':>11}  "
        f"{'nDCG':>11}  retrieval order from"
    )
    print(f"{'':<18} {'':>8} {'':>6}  {'judge / ret':>11}  {'judge / ret':>11}")
    for run in rows:
        labels, grade = run.labels, run.labels.grade
        mine = [one for one in run.judged if one.match.vacancy.key in labels.judged]
        # With a retrieval order, both columns are computed over the vacancies
        # it placed, so they are two answers to one question.
        if run.retrieval:
            mine = [one for one in mine if one.match.vacancy.key in run.retrieval]
        judge = concordance(
            (one.judgement.fit, grade(one.match.vacancy.key)) for one in mine
        )
        ret = concordance(
            (-run.retrieval[one.match.vacancy.key], grade(one.match.vacancy.key))
            for one in mine
            if run.retrieval
        )
        # `run.judged` is already in the judge's order (Judged.rank_key).
        judge_order = [one.match.vacancy.key for one in mine]
        retrieval_order = sorted(judge_order, key=lambda key: run.retrieval.get(key, 0))
        pairs = sum(
            1
            for i, a in enumerate(mine)
            for b in mine[i + 1 :]
            if grade(a.match.vacancy.key) != grade(b.match.vacancy.key)
        )
        ndcg = _pair(
            reordered_ndcg(judge_order, grade),
            reordered_ndcg(retrieval_order, grade) if run.retrieval else None,
        )
        print(
            f"{labels.cv:<18} {len(mine):>8} {pairs:>6}  "
            f"{_pair(judge, ret):>11}  {ndcg:>11}  "
            f"{run.retrieval_from or '(none stored)'}"
        )
    print(
        "\n  concordance: of every two vacancies graded differently, the share put "
        "the right\n  way round (0.5 is a coin). nDCG: the same vacancies in the "
        "judge's order and in\n  retrieval's, scored against their best order. "
        "Per CV, never averaged."
    )


def _pair(first: float | None, second: float | None) -> str:
    def one(value: float | None) -> str:
        return "  -" if value is None else f"{value:.2f}"

    return f"{one(first)} / {one(second)}"


def print_control(rows) -> None:
    for run in rows:
        labels, judged = run.labels, run.judged
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
    for run in rows:
        labels = run.labels
        outcome = assess(run.judged, corpus=corpus, corpus_name="raw")
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
        run.labels.cv
        for run in rows
        if assess(run.judged, corpus=corpus).fit is Fit.NO_CLEAR_FIT
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
