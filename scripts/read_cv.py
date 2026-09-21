"""Read a CV, show what is removed from it, and turn it into a profile.

This is the first half of CV matching, and it is a separate script on purpose:
before anything is sent to a model, you get to see exactly what would be sent.
The removal list holds your real phone number and e-mail, so it is printed on
your own terminal and written to no file.

Usage:
    uv run python scripts/read_cv.py data/samples/cvs/lisa_de_vries.md
    uv run python scripts/read_cv.py data/raw/cv/mahdi.pdf --strip-name "Mohammed Mahdi"
    uv run python scripts/read_cv.py data/samples/cvs/lisa_de_vries.pdf --show-sent
    uv run python scripts/read_cv.py data/raw/cv/mahdi.pdf --no-extract  # sends nothing

The model comes from the CV_* settings in .env, separately from LLM_* (vacancy
extraction) and EMBED_* (search), so where your CV goes is its own decision.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.cv.clean import Redacted, redact_cv
from joblens.cv.extract import extract_cv
from joblens.cv.read import CVDocument, UnreadableCVError, read_cv
from joblens.cv.schema import CVProfile
from joblens.llm.client import LLMClient
from joblens.llm.pricing import cost_usd, format_cost
from joblens.llm.structured import StructuredError, default_mode

ROOT = Path(__file__).parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cv", type=Path, help="a .pdf, .txt or .md CV")
    parser.add_argument(
        "--strip-name",
        metavar="NAME",
        help="remove this exact name from the text as well",
    )
    parser.add_argument(
        "--show-sent",
        action="store_true",
        help="print the full redacted text: exactly what would leave this machine",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="stop after redaction; makes no API call at all",
    )
    parser.add_argument("--json", type=Path, help="write the profile to this file")
    args = parser.parse_args()

    load_dotenv()
    try:
        document = read_cv(args.cv)
    except UnreadableCVError as err:
        print(err)
        return 1

    redacted = redact_cv(document.text, name=args.strip_name)
    report_reading(document, redacted)
    if args.show_sent:
        print("\n--- text that would be sent " + "-" * 51)
        print(redacted.text)
        print("-" * 79)
    if args.no_extract:
        print("\n--no-extract: nothing was sent anywhere.")
        return 0

    settings = load_llm_settings(prefix="CV")
    print(f"\nreading it with {settings.provider}/{settings.model}...")
    try:
        with LLMClient(settings) as client:
            result = extract_cv(redacted.text, client, mode=default_mode(settings))
    except (httpx.ConnectError, openai.APIConnectionError):
        print(f"Cannot reach {settings.base_url}. Is the model server running?")
        return 1
    except StructuredError as err:
        print(f"Could not read this CV into a profile:\n{err}")
        return 1

    report_profile(result.details)
    cost = cost_usd(settings, result.prompt_tokens, result.output_tokens)
    print(
        f"\n{result.prompt_tokens} tokens in, {result.output_tokens} out  |  "
        f"{format_cost(cost)}  |  {result.latency_s:.1f}s  |  "
        f"{result.attempts} attempt(s)"
    )
    if args.json:
        args.json.write_text(
            json.dumps(
                result.details.model_dump(mode="json"), indent=2, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        print(f"profile written to {args.json}")
    return 0


def report_reading(document: CVDocument, redacted: Redacted) -> None:
    pages = f", {document.pages} page(s)" if document.kind == "pdf" else ""
    print(f"{document.path}  ({document.kind}{pages}, {len(document.text)} chars)")
    print()
    if not redacted.removals:
        print("removed: nothing matched. Check the text before sending it.")
    else:
        summary = ", ".join(f"{n}x {kind}" for kind, n in redacted.counts().items())
        print(f"removed: {summary}")
        for removal in redacted.removals:
            print(f"  {removal.kind:<14} {shorten(removal.original)}")
    if not any(removal.kind == "name" for removal in redacted.removals):
        print(
            "\nnote: your name is still in the text that gets sent. Pass\n"
            '      --strip-name "Your Name" to remove it. Nothing else here can\n'
            "      find a name without guessing, and a guess deletes a skill."
        )


def report_profile(profile: CVProfile) -> None:
    years = profile.years_of_experience()
    # "covered by listed jobs", not "of experience": see CVProfile for why the
    # two are not the same number.
    shown = f"{years:g} years covered by listed jobs" if years else "no dated jobs"
    where = profile.city or "no city given"
    print(f"\n{profile.headline}  |  {where}  |  {shown}")
    if profile.summary:
        print(f"\n{profile.summary}")

    print(f"\nexperience ({len(profile.experience)})")
    for job in profile.experience:
        period = " - ".join(
            part
            for part in (job.start or "?", job.end or ("heden" if job.current else "?"))
        )
        at = f" @ {job.company}" if job.company else ""
        print(f"  {period:<18} {job.title}{at}")
        if job.skills:
            print(f"  {'':<18} {', '.join(job.skills)}")

    print(f"\neducation ({len(profile.education)})")
    for study in profile.education:
        level = f"[{study.level}]" if study.level else "[level unclear]"
        year = f" {study.end_year}" if study.end_year else ""
        mark = {True: "", False: " (not finished)", None: " (no diploma stated)"}[
            study.finished
        ]
        print(f"  {level:<16} {study.programme}{year}{mark}")

    print(f"\nskills ({len(profile.all_skills())}): {', '.join(profile.all_skills())}")
    if profile.certificates:
        print(f"certificates: {', '.join(profile.certificates)}")
    print(f"languages: {', '.join(profile.languages) or 'none stated'}")
    if profile.availability:
        print(f"availability: {profile.availability}")


def shorten(value: str, limit: int = 60) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


if __name__ == "__main__":
    sys.exit(main())
