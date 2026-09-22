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

Then two things about the run as a whole, both free: whether anything here fits
you at all (cv/outcome.py, counted off the verdicts because 3.5 measured that the
cosine cannot tell), and what keeps coming up that you do not have (cv/gaps.py,
counted off the gap lists and the extracted skills, with no extra model call).
The run is stored under data/raw/cv-runs/ so scripts/compare_runs.py can put two
of them side by side, or refuse to.

Costs about 0.35 cent and a couple of seconds per vacancy judged. --no-explain
skips that entirely and gives the retrieval order alone.
"""

import argparse
import sys
import textwrap
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.corpus import NAMES, load_corpus
from joblens.cv.documents import CV_STYLES
from joblens.cv.gaps import GapSummary, summarise_gaps
from joblens.cv.judge import PROMPT_VERSION, Judged, Verdict, judge_matches
from joblens.cv.match import prepare_cv, queries_for, search_with_cv
from joblens.cv.outcome import Fit, Outcome, assess
from joblens.cv.read import UnreadableCVError
from joblens.cv.runs import (
    RunStamp,
    build_record,
    corpus_digest,
    digest,
    save_run,
)
from joblens.cv.store import CVCache
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.store import CachedEmbedder
from joblens.llm.client import LLMClient
from joblens.llm.pricing import cost_usd, format_cost
from joblens.llm.structured import StructuredError, default_mode

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "cache"
# A run holds the CV and the text of real vacancies, so it is written where git
# is told never to look.
RUNS_DIR = ROOT / "data" / "raw" / "cv-runs"

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
        "--show-sent",
        action="store_true",
        help="print the redacted CV text: exactly what leaves this machine. "
        "read_cv.py shows the same thing before anything is sent at all",
    )
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
            report_cv_problems(prepared)
            if args.show_sent:
                print("\n--- text that was sent " + "-" * 55)
                print(prepared.text)
                print("-" * 78 + "\n")
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

    outcome = assess(judged, corpus=len(corpus), corpus_name=args.corpus)
    summary = summarise_gaps(judged, prepared.profile, corpus.details)

    banner(outcome)
    for position, one in enumerate(judged, 1):
        show(position, one)
    for failure in failures:
        print(f"\n  could not be judged: {failure}")
    show_gaps(summary, outcome)
    stamp = RunStamp(
        cv_name=prepared.name,
        cv_digest=digest(prepared.text),
        corpus=corpus.name,
        corpus_size=len(corpus),
        corpus_digest=corpus_digest(corpus.vacancies),
        embed_model=embed_settings.model,
        judge_model=cv_settings.model,
        cv_style=args.style,
        prompt_version=PROMPT_VERSION,
        top=args.top,
    )
    footer(judged, prepared, stamp, summary, failures, cv_settings)
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


def report_cv_problems(prepared) -> None:
    """Whatever is wrong with the CV itself, before any vacancy is discussed."""
    damage = prepared.document.damage
    if damage.reader_warnings:
        print(
            f"note: pypdf reported {damage.reader_warnings} warnings about broken "
            "font data in this file."
        )
    for line in damage.report():
        print(line)
    if prepared.dropped_dates:
        dates = ", ".join(f"{d.value} ({d.where})" for d in prepared.dropped_dates[:6])
        print(
            f"!! {len(prepared.dropped_dates)} date(s) the model produced are not "
            f"in the CV and were dropped: {dates}"
        )
    if damage or prepared.dropped_dates:
        print()


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


def banner(outcome: Outcome) -> None:
    """The honest answer about the run, above the list rather than under it.

    Above, because a refusal printed after ten formatted vacancies has already
    been contradicted by the time you reach it. The list still prints either way:
    the brief asks for "never silence, never a padded list", and a refusal that
    hid the closest few would be the first of those.
    """
    if outcome.fit is Fit.OK:
        return
    rule = "=" * 78
    print(f"\n{rule}\n{textwrap.fill(outcome.headline(), 78)}")
    for line in outcome.advice():
        print(textwrap.fill(line, 78, initial_indent="", subsequent_indent=""))
    print(rule)


def show_gaps(summary: GapSummary, outcome: Outcome) -> None:
    """What keeps coming up that you do not have. No model was asked."""
    print("\n" + "=" * 78)
    print(f"WHAT KEEPS COMING UP THAT YOU DO NOT HAVE  ({summary.judged} judged)")
    if not summary.groups and not summary.fields:
        print("  Nothing appeared more than as itself: no requirement recurs.")
        return

    for position, group in enumerate(summary.groups[:8], 1):
        example = group.example()
        required = (
            f", {group.required_count} as a requirement" if group.required_count else ""
        )
        print(
            f"\n{position:>2}. {group.term}  —  in {group.count} of "
            f"{summary.judged}{required} · avg fit {group.average_fit:.0f} · "
            f"weight {group.weight:.1f}"
        )
        print(f"      e.g. {shorten(example.title, 60)}:")
        print(f'      "{shorten(example.quote)}"')

    if summary.fields:
        print("\n  from the extracted fields, no judge involved:")
        for one in summary.fields:
            print(f"    - {one.label}: {one.count} of {one.total} — {one.detail}")

    print(
        "\n  weight is the sum of each match's fit/100, so a gap in a vacancy you "
        "\n  nearly fit counts for more than the same gap in one you do not."
    )
    if summary.ungrouped:
        print(
            f"  {len(summary.ungrouped)} gap(s) named nothing these vacancies list "
            f"as a skill and\n  could not be grouped "
            f"({summary.grouped_share:.0%} of {summary.total_gaps} were)."
        )
    if summary.already_on_cv:
        print(
            f"  {len(summary.already_on_cv)} gap(s) asked for something your CV does "
            "list, and are left out\n  of the count above: "
            + ", ".join(sorted({one.requirement for one in summary.already_on_cv})[:3])
        )
    if outcome.fit is not Fit.OK:
        print("\n  " + outcome.headline())


def footer(judged, prepared, stamp, summary, failures, cv_settings) -> None:
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
    if dropped and prepared.document.damage:
        # Measured on a real CV in 3.6.1: every dropped quote but one was the
        # model quietly repairing a glyph the font had eaten -- writing
        # "Supabase (PostgreSQL)" where the text it was given says
        # "Supabase ?PostgreSQL?". The check is right to refuse it, and the
        # thing to fix is the PDF, so the two numbers are printed together.
        print(
            "This CV has unreadable characters in it, which is the likeliest "
            "reason: a\nquote of a damaged line cannot match the damaged line."
        )
    cost = cost_usd(cv_settings, tokens_in, tokens_out)
    print(
        f"{tokens_in} tokens in, {tokens_out} out  |  {format_cost(cost)}  |  "
        f"{seconds:.0f}s of model time"
    )
    outcome = assess(judged, corpus=stamp.corpus_size, corpus_name=stamp.corpus)
    record = build_record(
        stamp, judged, outcome, summary, failures=failures, cost_usd=cost
    )
    path = save_run(record, RUNS_DIR)
    print(f"run: {stamp.line()}")
    print(f"saved: {path.relative_to(ROOT)}  (compare_runs.py reads these)")
    print(
        "Scores compare vacancies inside this run only. Another CV, another "
        "embedder or another prompt version is a different scale, and "
        "compare_runs.py refuses those rather than subtracting them."
    )


def shorten(value: str, limit: int = 96) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


if __name__ == "__main__":
    sys.exit(main())
