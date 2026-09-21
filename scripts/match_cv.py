"""Rank real vacancies against a CV.

    uv run python scripts/match_cv.py data/samples/cvs/lisa_de_vries.md
    uv run python scripts/match_cv.py data/raw/cv/mahdi.pdf --strip-name "M Mahdi"
    uv run python scripts/match_cv.py data/samples/cvs/sanne_vermeulen.md --style roles

This is retrieval only: it says which vacancies are closest to this CV, not why,
and not what is missing. The explanation is milestone 3.6, and until it exists the
scores here are a similarity and nothing more -- 0.82 is "closer than 0.79", never
"82% a match", and two runs from two different CVs cannot be compared at all.

The default `--style raw` sends the whole redacted CV as one query. It was not the
obvious choice and it won on numbers: see the 3.5 entry in docs/learning-log.md.
"""

import argparse
import sys
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import NAMES, load_corpus
from joblens.cv.documents import CV_STYLES
from joblens.cv.match import prepare_cv, queries_for, search_with_cv
from joblens.cv.read import UnreadableCVError
from joblens.cv.store import CVCache
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.store import CachedEmbedder
from joblens.llm.client import LLMClient
from joblens.llm.pricing import cost_usd, format_cost
from joblens.llm.structured import StructuredError, default_mode

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "cache"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cv", type=Path)
    parser.add_argument("--corpus", choices=NAMES, default="raw")
    parser.add_argument("--style", choices=CV_STYLES, default="raw")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--strip-name", metavar="NAME")
    args = parser.parse_args()

    load_dotenv()
    corpus = load_corpus(args.corpus).extracted()
    if not corpus.vacancies:
        print(f"No extracted vacancies in the {args.corpus} corpus. Index some first.")
        return 1

    cv_settings = load_llm_settings(prefix="CV")
    embed_settings = load_llm_settings(prefix="EMBED")
    cache = CVCache(CACHE_DIR / "cv-profiles.json")
    try:
        with LLMClient(cv_settings) as client:
            prepared = prepare_cv(
                args.cv,
                client,
                name=args.strip_name,
                model=cv_settings.model,
                mode=default_mode(cv_settings),
                cache=cache,
            )
            parts = queries_for(
                prepared,
                args.style,
                client=client,
                model=cv_settings.model,
                cache=cache,
            )
        with EmbeddingClient(embed_settings) as embedder:
            path = (
                CACHE_DIR / f"embeddings-{embed_settings.model.replace(':', '-')}.json"
            )
            index = VacancyIndex.build(
                corpus.vacancies, corpus.details, CachedEmbedder(embedder, path)
            )
            matches = search_with_cv(index, parts, top_k=args.top)
    except UnreadableCVError as err:
        print(err)
        return 1
    except StructuredError as err:
        print(f"Could not read this CV into a profile:\n{err}")
        return 1
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1

    report(prepared, args, len(index), matches, cv_settings, embed_settings, parts)
    return 0


def report(prepared, args, indexed, matches, cv_settings, embed_settings, parts):
    counts = prepared.redacted.counts()
    removed = ", ".join(f"{n}x {kind}" for kind, n in counts.items()) or "nothing"
    print(f"{prepared.name}: {prepared.profile.headline}  |  removed: {removed}")
    print(
        f"{indexed} vacancies from the {args.corpus} corpus, embedded by "
        f"{embed_settings.model}  |  CV read by {cv_settings.model}"
        + ("  (cached)" if prepared.from_cache else "")
    )
    print(f"searching as {args.style}: {len(parts)} query part(s)\n")

    for position, match in enumerate(matches, 1):
        vacancy = match.vacancy
        where = " · ".join(p for p in (vacancy.company, vacancy.city) if p)
        print(
            f"{position:>2}. {match.score:.3f}  {vacancy.title[:48]:<48} {where[:30]}"
        )
        if len(parts) > 1:
            print(f"           matched: {match.part.label}")
        if vacancy.url.startswith("http"):
            print(f"           {vacancy.url}")

    cost = cost_usd(cv_settings, prepared.prompt_tokens, prepared.output_tokens)
    print(
        f"\nreading the CV: {prepared.prompt_tokens} tokens in, "
        f"{prepared.output_tokens} out, {format_cost(cost)}, "
        f"{prepared.latency_s:.1f}s"
        if not prepared.from_cache
        else "\nthe CV was already read; this run only embedded it."
    )
    print(
        "scores are cosine similarity: comparable inside this list, and nowhere "
        "else. Why each one matched is milestone 3.6."
    )


if __name__ == "__main__":
    sys.exit(main())
