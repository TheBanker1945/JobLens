"""Pool the candidates a CV should be judged on, for judging somewhere else.

The eval can only measure what someone has judged. Candidates are **pooled**: any
vacancy that any variant in evals/cv-matching.toml puts in its top N, so a variant
that surfaces a good vacancy nobody was asked about is not scored wrong for
finding it.

**The y/m/n prompt that used to live here is gone (4.4).** It produced exactly
the labels that started phase 3: a key in one of three lists, with no sentence
saying why, so a disagreement between the judge and the person could not be
read by either of them afterwards. Marking now happens in the viewer, where a
call needs a one-line reason and the vacancy text is on the same screen:

    uv run python scripts/serve.py --judged-by "your name"

What this script still does is pool, which the viewer cannot: the viewer shows
one run in one configuration, and pooling asks every variant in the eval config
what it would have surfaced.

Usage:
    uv run python scripts/label_cv_matches.py --dump notes.md    # judge offline
    uv run python scripts/label_cv_matches.py --cv data/raw/cv/mahdi.pdf --dump n.md
    uv run python scripts/label_cv_matches.py --pool-depth 5     # fewer candidates
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
from joblens.evals.matching import MatchConfig, rank_vacancies
from joblens.evals.retrieval import EmbedderPool
from joblens.llm.client import LLMClient
from joblens.sources.base import Vacancy

ROOT = Path(__file__).parent.parent
SAMPLE_CVS = ROOT / "data" / "samples" / "cvs"
CACHE_DIR = ROOT / "data" / "cache"
CONFIG = ROOT / "evals" / "cv-matching.toml"

VIEWER = (
    "Marking happens in the viewer now, with a reason attached to every call:\n"
    "    uv run python scripts/serve.py --judged-by 'your name'"
)


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
    args = parser.parse_args()

    load_dotenv()
    # README.md lives in that folder too, and it is not a person.
    cvs = args.cv or [
        path for path in sorted(SAMPLE_CVS.glob("*.md")) if path.stem != "README"
    ]
    if not cvs:
        print("No CVs to label.")
        return 1
    if not args.dump:
        print(f"Nothing to do without --dump.\n\n{VIEWER}")
        return 1

    corpus = load_corpus(args.corpus).extracted()
    configs = [MatchConfig.model_validate(c) for c in _runs(args.config)]
    settings = load_llm_settings(prefix="CV")
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
                notes.append(dump_one(prepared, corpus, candidates))
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1

    args.dump.write_text("\n".join(notes), encoding="utf-8")
    print(f"wrote {args.dump} ({len(notes)} CVs)\n\n{VIEWER}")
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


def dump_one(
    prepared: PreparedCV, corpus: Corpus, candidates: dict[str, list[str]]
) -> str:
    """The same candidates as a file, for judging away from a prompt."""
    by_key = corpus.by_key()
    lines = [
        f"# {prepared.name} — {prepared.profile.headline}",
        "",
        f"{len(candidates)} pooled candidates. Every call needs a reason; the "
        "viewer asks for one and stores it with the run you were looking at.",
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
