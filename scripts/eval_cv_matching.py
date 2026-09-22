"""How should a CV ask the index a question? Compared over the labelled CVs.

Usage:
    uv run python scripts/eval_cv_matching.py
    uv run python scripts/eval_cv_matching.py --only gemini
    uv run python scripts/eval_cv_matching.py --mistakes

Every row is one CV. Nothing is averaged across CVs, and the reason is in
joblens/evals/matching.py: there are three of them, one of them is a joke CV that
should match nothing, and a mean over that is a number with no meaning. A variant
wins here by winning on each CV separately.

The last table needs no labels at all: how much two people's top ten overlap. If
two different CVs get the same list, the app is not reading them, and every other
number on the page is measuring something else.
"""

import argparse
import datetime
import json
import sys
import tomllib
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import Corpus, load_corpus
from joblens.cv.match import PreparedCV, prepare_cv, queries_for
from joblens.cv.read import CV_DIRECTORIES, find_cv
from joblens.cv.store import CVCache
from joblens.embeddings.documents import build_document
from joblens.evals.matching import (
    CVLabels,
    CVResult,
    MatchConfig,
    MatchRun,
    overlap,
    rank_vacancies,
)
from joblens.evals.retrieval import EmbedderPool
from joblens.llm.client import LLMClient
from joblens.storage import FileStore

ROOT = Path(__file__).parent.parent
SAMPLE_CVS = ROOT / "data" / "samples" / "cvs"
CACHE_DIR = ROOT / "data" / "cache"
RESULTS_DIR = ROOT / "evals" / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "evals/cv-matching.toml")
    parser.add_argument("--corpus", default="raw")
    parser.add_argument("--only", help="only variants whose name contains this text")
    parser.add_argument(
        "--cv-dir", type=Path, help="look here first (default: samples, then raw)"
    )
    parser.add_argument(
        "--strip-name", metavar="NAME", help="remove this name, as the labelling did"
    )
    parser.add_argument(
        "--mistakes", action="store_true", help="what the best variant put on top"
    )
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    labels = FileStore(ROOT).labels()
    if not labels:
        print(
            "No judged CVs in evals/cv-matches or data/raw/cv-labels yet.\n"
            "Judge some first: uv run python scripts/label_cv_matches.py"
        )
        return 1
    corpus = load_corpus(args.corpus).extracted()
    runs = tomllib.loads(args.config.read_text(encoding="utf-8"))["run"]
    configs = [
        MatchConfig.model_validate(run)
        for run in runs
        if not args.only or args.only in run["name"]
    ]
    settings = load_llm_settings(prefix="CV")
    cache = CVCache(CACHE_DIR / "cv-profiles.json")

    print(f"corpus {corpus.name}: {len(corpus)} vacancies x {len(configs)} variants")
    for one in labels:
        print(
            f"  {one.cv:<18} {len(one.relevant):>3} would apply, "
            f"{len(one.maybe):>3} maybe, {len(one.judged):>3} judged "
            f"(by {one.judged_by})"
        )

    results: list[MatchRun] = []
    try:
        with LLMClient(settings) as client, EmbedderPool(CACHE_DIR) as pool:
            prepared = {
                one.cv: find_and_prepare(
                    args.cv_dir, one, client, settings.model, cache
                )
                for one in labels
            }
            for config in configs:
                print(f"\nrunning {config.name} ...", flush=True)
                results.append(
                    run_variant(
                        config,
                        corpus,
                        labels,
                        prepared,
                        client,
                        settings.model,
                        cache,
                        pool,
                    )
                )
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1

    print_per_cv(results, labels)
    print_control(results, labels)
    print_winners(results, labels)
    print_overlap(results, labels)
    if args.mistakes:
        print_mistakes(results[0], labels, corpus)
    if not args.no_save:
        print(f"\nsaved: {save(results).relative_to(ROOT)}")
    return 0


def find_and_prepare(
    directory: Path | None,
    labels: CVLabels,
    client,
    model: str,
    cache: CVCache,
    strip_name: str | None = None,
) -> PreparedCV:
    directories = (directory, *CV_DIRECTORIES) if directory else CV_DIRECTORIES
    path = find_cv(labels.cv, directories)
    if path is None:
        raise SystemExit(
            f"No CV file for {labels.cv!r}. Looked in "
            + ", ".join(str(d) for d in directories)
        )
    return prepare_cv(path, client, name=strip_name, model=model, cache=cache)


def run_variant(
    config: MatchConfig,
    corpus: Corpus,
    labels: list[CVLabels],
    prepared: dict[str, PreparedCV],
    client,
    model: str,
    cache: CVCache,
    pool: EmbedderPool,
) -> MatchRun:
    documents = [
        build_document(v.text, corpus.details.get(v.key), config.style)
        for v in corpus.vacancies
    ]
    results, seconds, calls = [], 0.0, 0
    for one in labels:
        parts = queries_for(
            prepared[one.cv], config.cv_style, client=client, model=model, cache=cache
        )
        ranking, embedded = rank_vacancies(config, documents, parts, CACHE_DIR, pool)
        seconds += embedded.seconds
        calls += embedded.api_calls
        results.append(
            CVResult(
                cv=one.cv,
                ranked=[corpus.vacancies[i].key for i in ranking.order],
                relevant=one.relevant,
                maybe=one.maybe,
                top_score=ranking.scores[0],
            )
        )
    return MatchRun(
        variant=config.name,
        cv_style=config.cv_style,
        model=config.model,
        results=results,
        seconds=seconds,
        api_calls=calls,
    )


def print_per_cv(results: list[MatchRun], labels: list[CVLabels]) -> None:
    for one in labels:
        if not one.relevant and not one.maybe:
            continue  # a control CV: print_control has it
        print(f"\n===== {one.cv} =====")
        print(
            f"{'variant':<22} {'hit@1':>6} {'recall@10':>10} {'MRR':>6} "
            f"{'nDCG@10':>8} {'labelled':>9}"
        )
        judged = set(one.judged)
        worst = 1.0
        for run in results:
            found = run.for_cv(one.cv)
            if not found:
                continue
            covered = found.labelled_share(judged)
            worst = min(worst, covered)
            print(
                f"{run.variant:<22} {found.hit_at_1:>6.0%} "
                f"{found.recall_at(10):>10.0%} "
                f"{found.reciprocal_rank:>6.2f} {found.ndcg_at(10):>8.2f} "
                f"{covered:>9.0%}"
            )
        if worst < 1.0:
            print(
                f"  'labelled' is how much of each top 10 was pooled when "
                f"{one.cv} was judged.\n  Below 100% the scores above are a "
                f"floor: an unlabelled vacancy scores like a\n  bad one, so a "
                f"variant is punished for finding something nobody read."
            )


def print_winners(results: list[MatchRun], labels: list[CVLabels]) -> None:
    """Which variant is best on each CV, counted. No averaging: see the module."""
    print("\n===== best per CV (nDCG@10) =====")
    tally: dict[str, int] = {}
    for one in labels:
        if not one.relevant and not one.maybe:
            continue
        scored = [
            (run.variant, run.for_cv(one.cv).ndcg_at(10))
            for run in results
            if run.for_cv(one.cv)
        ]
        if not scored:
            continue
        best = max(score for _, score in scored)
        winners = [name for name, score in scored if score == best]
        for name in winners:
            tally[name] = tally.get(name, 0) + 1
        print(f"  {one.cv:<18} {', '.join(winners)}  ({best:.2f})")
    print(
        "\n  won on:",
        ", ".join(f"{n} x{c}" for n, c in sorted(tally.items(), key=lambda kv: -kv[1])),
    )


def print_control(results: list[MatchRun], labels: list[CVLabels]) -> None:
    """A CV that fits nothing still gets a number one. This is how big it is.

    Ranking metrics cannot say anything here -- there is no right answer to rank
    first -- so what is printed is the top score itself, next to the top score of
    a CV that does fit. If a refusal is ever going to be a threshold, the gap
    between those two columns is the whole of the evidence for it.
    """
    controls = [one for one in labels if not one.relevant and not one.maybe]
    real = [one for one in labels if one.relevant]
    if not controls:
        return
    for one in controls:
        print(f"\n===== {one.cv} (control: nothing in this corpus fits) =====")
        others = "  ".join(f"{o.cv[:12]:>14}" for o in real)
        print(f"{'variant':<22} {'top score':>10}{others}")
        for run in results:
            found = run.for_cv(one.cv)
            if not found:
                continue
            cells = "".join(
                f"{run.for_cv(o.cv).top_score:>14.3f}" if run.for_cv(o.cv) else " " * 14
                for o in real
            )
            print(f"{run.variant:<22} {found.top_score:>10.3f}{cells}")


def print_overlap(results: list[MatchRun], labels: list[CVLabels]) -> None:
    """Do two different CVs get two different lists? No labels needed."""
    if len(labels) < 2:
        return
    pairs = [(a.cv, b.cv) for i, a in enumerate(labels) for b in labels[i + 1 :]]
    print("\n===== top-10 overlap between CVs (lower is better) =====")
    header = "  ".join(f"{a[:6]}/{b[:6]}" for a, b in pairs)
    print(f"{'variant':<22} {header}")
    for run in results:
        cells = [f"{overlap(run.top(a), run.top(b)):>13.0%}" for a, b in pairs]
        print(f"{run.variant:<22}{''.join(cells)}")


def print_mistakes(run: MatchRun, labels: list[CVLabels], corpus: Corpus) -> None:
    known = corpus.by_key()
    for one in labels:
        found = run.for_cv(one.cv)
        if not found:
            continue
        print(f"\n--- {run.variant} on {one.cv}: top 5")
        for position, key in enumerate(found.ranked[:5], 1):
            mark = {2: "apply", 1: "maybe"}.get(one.grade(key), "no")
            title = known[key].title if key in known else f"<{key} gone>"
            print(f"  {position}. [{mark:<5}] {title}")


def save(results: list[MatchRun]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    path = RESULTS_DIR / f"{stamp}_cv_matching.json"
    path.write_text(
        json.dumps([r.model_dump() for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    sys.exit(main())
