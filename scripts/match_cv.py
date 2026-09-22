"""Rank real vacancies against a CV, with the evidence for each one.

    uv run python scripts/match_cv.py data/samples/cvs/lisa_de_vries.md
    uv run python scripts/match_cv.py data/raw/cv/mahdi.pdf --strip-name "M Mahdi"
    uv run python scripts/match_cv.py data/samples/cvs/sanne_vermeulen.md --top 20
    uv run python scripts/match_cv.py data/samples/cvs/ingrid_solheim.md --no-explain

Two stages. Embeddings pick a shortlist out of the whole corpus, cheaply and
without understanding anything. A model then reads each shortlisted vacancy next
to the CV and says how well it fits, which lines of the CV answer it, and what it
asks for that the CV does not show -- and **every quote it produces is checked
against the source text before you see it**. A quote that is not there loses its
claim.

Costs about 0.35 cent and a couple of seconds per vacancy judged. --no-explain
skips that entirely and gives the retrieval order alone.
"""

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import NAMES, load_corpus
from joblens.cv.documents import CV_STYLES
from joblens.cv.judge import PROMPT_VERSION, Judged, Verdict, judge_matches
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

BADGE = {
    Verdict.STRONG: "STRONG  ",
    Verdict.POSSIBLE: "possible",
    Verdict.WEAK: "weak    ",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cv", type=Path)
    parser.add_argument("--corpus", choices=NAMES, default="raw")
    parser.add_argument("--style", choices=CV_STYLES, default="raw")
    parser.add_argument("--top", type=int, default=10, help="how many to judge")
    parser.add_argument("--strip-name", metavar="NAME")
    parser.add_argument(
        "--no-explain", action="store_true", help="retrieval only: no model calls"
    )
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
                name = embed_settings.model.replace(":", "-")
                index = VacancyIndex.build(
                    corpus.vacancies,
                    corpus.details,
                    CachedEmbedder(embedder, CACHE_DIR / f"embeddings-{name}.json"),
                )
                matches = search_with_cv(index, parts, top_k=args.top)

            header(prepared, args, len(index), len(parts), embed_settings, cv_settings)
            if args.no_explain:
                for position, match in enumerate(matches, 1):
                    print(f"{position:>2}. {match.score:.3f}  {match.vacancy.title}")
                print("\nno explanations asked for: nothing was judged.")
                return 0

            print(f"judging {len(matches)} vacancies ...", flush=True)
            judged, failures = judge_matches(
                prepared.text, matches, client, mode=default_mode(cv_settings)
            )
    except UnreadableCVError as err:
        print(err)
        return 1
    except StructuredError as err:
        print(f"Could not read this CV into a profile:\n{err}")
        return 1
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1

    for position, one in enumerate(judged, 1):
        show(position, one)
    for failure in failures:
        print(f"\n  could not be judged: {failure}")
    footer(judged, prepared, corpus, args, cv_settings, embed_settings)
    return 0


def header(prepared, args, indexed, parts, embed_settings, cv_settings) -> None:
    counts = prepared.redacted.counts()
    removed = ", ".join(f"{n}x {kind}" for kind, n in counts.items()) or "nothing"
    print(f"{prepared.name}: {prepared.profile.headline}  |  removed: {removed}")
    print(
        f"{indexed} vacancies from the {args.corpus} corpus, embedded by "
        f"{embed_settings.model}; shortlist of {args.top} as {args.style} "
        f"({parts} query part(s))"
    )
    print(f"judged by {cv_settings.model}, prompt {PROMPT_VERSION}\n")


def show(position: int, one: Judged) -> None:
    vacancy, judgement = one.match.vacancy, one.judgement
    where = " · ".join(p for p in (vacancy.company, vacancy.city) if p)
    print(
        f"\n{position:>2}. [{BADGE[judgement.verdict]} {judgement.fit:>3}] "
        f"{vacancy.title}"
    )
    print(f"    {where}" if where else "")
    print(f"    {judgement.summary}")
    if judgement.evidence:
        print("    why it fits:")
        for item in judgement.evidence:
            print(f"      + {item.requirement}")
            print(f'        your CV: "{shorten(item.cv_quote)}"')
    if judgement.gaps:
        print("    what you are missing:")
        for gap in judgement.gaps:
            mark = "required" if gap.required else "a plus"
            print(f"      - {gap.requirement}  ({mark})")
            print(f'        the vacancy: "{shorten(gap.vacancy_quote)}"')
    if one.dropped:
        print(f"    {len(one.dropped)} claim(s) dropped: the quote was not in the text")
    if vacancy.url.startswith("http"):
        print(f"    {vacancy.url}")


def footer(judged, prepared, corpus, args, cv_settings, embed_settings) -> None:
    tokens_in = sum(one.prompt_tokens for one in judged)
    tokens_out = sum(one.output_tokens for one in judged)
    seconds = sum(one.latency_s for one in judged)
    quotes = sum(one.quotes for one in judged)
    dropped = sum(len(one.dropped) for one in judged)
    verified = (quotes - dropped) / quotes if quotes else 1.0
    counts = {verdict: 0 for verdict in Verdict}
    for one in judged:
        counts[one.judgement.verdict] += 1

    print("\n" + "-" * 78)
    print(
        "  ".join(f"{counts[v]} {v}" for v in Verdict)
        + f"  |  {quotes} quotes checked, {verified:.0%} found in the source"
        + (f", {dropped} dropped" if dropped else "")
    )
    cost = cost_usd(cv_settings, tokens_in, tokens_out)
    print(
        f"{tokens_in} tokens in, {tokens_out} out  |  {format_cost(cost)}  |  "
        f"{seconds:.0f}s of model time"
    )
    print(f"run: {stamp(prepared, corpus, args, cv_settings, embed_settings)}")
    print(
        "Scores compare vacancies inside this run only. Another CV, another day's "
        "corpus or another prompt version is a different scale."
    )


def stamp(prepared, corpus, args, cv_settings, embed_settings) -> str:
    """What would have to be equal for two runs to be comparable at all."""
    digest = hashlib.sha256(prepared.text.encode()).hexdigest()[:8]
    return (
        f"cv {digest} · {corpus.name} corpus of {len(corpus)} · "
        f"{embed_settings.model} · {cv_settings.model} · prompt {PROMPT_VERSION} · "
        f"{datetime.now():%Y-%m-%d %H:%M}"
    )


def shorten(value: str, limit: int = 96) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


if __name__ == "__main__":
    sys.exit(main())
