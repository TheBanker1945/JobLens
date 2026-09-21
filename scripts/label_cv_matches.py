"""Judge which vacancies a CV should actually match. A human sits here.

The eval can only measure what someone has judged. Candidates are **pooled**: any
vacancy that any variant in evals/cv-matching.toml puts in its top N, so a variant
that surfaces a good vacancy nobody was asked about is not scored wrong for
finding it.

Three answers, not two. "Would apply" and "would not" are the ends; "might" sits
between them and is what nDCG uses, because a job list is not a set of right
answers -- it is an ordering, and second-best has to be worth something.

Usage:
    uv run python scripts/label_cv_matches.py                    # the sample CVs
    uv run python scripts/label_cv_matches.py --cv data/raw/cv/mahdi.pdf
    uv run python scripts/label_cv_matches.py --dump notes.md    # judge offline
    uv run python scripts/label_cv_matches.py --pool-depth 5     # fewer candidates

Stop whenever you like with q: every answer so far is already saved.
"""

import argparse
import sys
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import Corpus, load_corpus
from joblens.cv.match import PreparedCV, prepare_cv, queries_for
from joblens.cv.store import CVCache
from joblens.embeddings.documents import build_document
from joblens.evals.matching import (
    CVLabels,
    MatchConfig,
    load_labels,
    rank_vacancies,
    save_labels,
)
from joblens.evals.retrieval import EmbedderPool
from joblens.llm.client import LLMClient
from joblens.sources.base import Vacancy

ROOT = Path(__file__).parent.parent
SAMPLE_CVS = ROOT / "data" / "samples" / "cvs"
LABELS_DIR = ROOT / "evals" / "cv-matches"
CACHE_DIR = ROOT / "data" / "cache"
CONFIG = ROOT / "evals" / "cv-matching.toml"

MENU = "  would you apply? [y]es  [m]aybe  [n]o  [v]iew text  [s]kip CV  [q]uit"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cv", type=Path, action="append", help="a CV file")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--corpus", default="raw")
    parser.add_argument("--pool-depth", type=int, default=10)
    parser.add_argument("--strip-name", help="a name to remove from every CV")
    parser.add_argument(
        "--dump", type=Path, help="write the candidates to a file and judge offline"
    )
    parser.add_argument(
        "--judged-by", default="", help="whose opinion these labels are"
    )
    args = parser.parse_args()

    load_dotenv()
    # README.md lives in that folder too, and it is not a person.
    cvs = args.cv or [
        path for path in sorted(SAMPLE_CVS.glob("*.md")) if path.stem != "README"
    ]
    if not cvs:
        print("No CVs to label.")
        return 1
    if not args.dump and not args.judged_by:
        print("Say who is judging: --judged-by 'your name'")
        return 1

    corpus = load_corpus(args.corpus).extracted()
    configs = [MatchConfig.model_validate(c) for c in _runs(args.config)]
    settings = load_llm_settings(prefix="CV")
    existing = {labels.cv: labels for labels in load_labels(LABELS_DIR)}
    cache = CVCache(CACHE_DIR / "cv-profiles.json")
    print(f"corpus {corpus.name}: {len(corpus)} vacancies, {len(configs)} variants")

    notes: list[str] = []
    try:
        with LLMClient(settings) as client, EmbedderPool(CACHE_DIR) as pool:
            for path in cvs:
                prepared = prepare_cv(
                    path,
                    client,
                    name=args.strip_name,
                    model=settings.model,
                    cache=cache,
                )
                candidates = pooled_candidates(
                    prepared,
                    corpus,
                    configs,
                    settings.model,
                    client,
                    cache,
                    args.pool_depth,
                    pool,
                )
                if args.dump:
                    notes.append(dump_one(prepared, corpus, candidates))
                    continue
                labels = existing.get(prepared.name) or CVLabels(
                    cv=prepared.name, corpus=corpus.name, judged_by=args.judged_by
                )
                labels.judged_by = args.judged_by
                if judge(prepared, corpus, candidates, labels) == "quit":
                    save_labels(LABELS_DIR, labels)
                    print("saved; stopping.")
                    return 0
                print(f"saved: {save_labels(LABELS_DIR, labels).relative_to(ROOT)}")
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1

    if args.dump:
        args.dump.write_text("\n".join(notes), encoding="utf-8")
        print(f"wrote {args.dump} ({len(notes)} CVs)")
    return 0


def _runs(path: Path) -> list[dict]:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))["run"]


def pooled_candidates(
    prepared: PreparedCV,
    corpus: Corpus,
    configs: list[MatchConfig],
    model: str,
    client: LLMClient,
    cache: CVCache,
    depth: int,
    pool: EmbedderPool,
) -> dict[str, list[str]]:
    """Every vacancy any variant ranks in its top `depth`, and who ranked it.

    Ordered by how many variants found it: the ones everything agrees on are
    worth judging first, and a session that stops halfway has then judged the
    candidates that matter most.
    """
    found: dict[str, list[str]] = {}
    for config in configs:
        documents = [
            build_document(v.text, corpus.details.get(v.key), config.style)
            for v in corpus.vacancies
        ]
        parts = queries_for(
            prepared, config.cv_style, client=client, model=model, cache=cache
        )
        ranking, _ = rank_vacancies(config, documents, parts, CACHE_DIR, pool)
        for rank, index in enumerate(ranking.order[:depth], 1):
            key = corpus.vacancies[index].key
            found.setdefault(key, []).append(f"{config.name} #{rank}")
    return dict(sorted(found.items(), key=lambda kv: -len(kv[1])))


def judge(
    prepared: PreparedCV,
    corpus: Corpus,
    candidates: dict[str, list[str]],
    labels: CVLabels,
) -> str:
    by_key = corpus.by_key()
    todo = [key for key in candidates if key not in labels.judged]
    print(f"\n=== {prepared.name}: {prepared.profile.headline}")
    print(f"    {len(candidates)} candidates, {len(todo)} not yet judged")
    for position, key in enumerate(todo, 1):
        vacancy = by_key[key]
        print(f"\n[{position}/{len(todo)}] {vacancy.title}")
        print(f"    {vacancy.company or '?'} · {vacancy.city or '?'} · {key}")
        print(f"    found by: {', '.join(candidates[key][:4])}")
        while True:
            answer = input(MENU + "\n  > ").strip().lower()
            if answer == "v":
                print(vacancy.text[:1500])
                continue
            if answer in {"y", "m", "n"}:
                labels.judged.append(key)
                if answer == "y":
                    labels.relevant.append(key)
                elif answer == "m":
                    labels.maybe.append(key)
                break
            if answer in {"s", "q"}:
                return "quit" if answer == "q" else "skip"
    return "done"


def dump_one(
    prepared: PreparedCV, corpus: Corpus, candidates: dict[str, list[str]]
) -> str:
    """The same candidates as a file, for judging away from a prompt."""
    by_key = corpus.by_key()
    lines = [
        f"# {prepared.name} — {prepared.profile.headline}",
        "",
        f"{len(candidates)} pooled candidates. Mark each one y (would apply), "
        "m (might), or n.",
        "",
    ]
    for key in candidates:
        lines += _candidate_lines(by_key[key], candidates[key], corpus)
    return "\n".join(lines)


def _candidate_lines(
    vacancy: Vacancy, found_by: list[str], corpus: Corpus
) -> list[str]:
    details = corpus.details.get(vacancy.key)
    summary = build_document(vacancy.text, details, "structured") if details else ""
    body = " ".join(vacancy.text.split())[:700]
    return [
        f"## {vacancy.key} — {vacancy.title}",
        f"found by: {', '.join(found_by)}",
        "",
        "```",
        summary,
        "```",
        "",
        body,
        "",
        "---",
        "",
    ]


if __name__ == "__main__":
    sys.exit(main())
