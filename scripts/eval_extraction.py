"""Evaluate extraction quality for every setting in evals/extraction.toml.

Usage:
    uv run python scripts/eval_extraction.py                 # all runs, save results
    uv run python scripts/eval_extraction.py --only think    # runs whose name matches
    uv run python scripts/eval_extraction.py --mistakes      # list every wrong field
"""

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.evals.runner import RunResult, load_configs, load_samples, run_eval
from joblens.llm.client import LLMClient

ROOT = Path(__file__).parent.parent
SAMPLES_DIR = ROOT / "data" / "samples"
RESULTS_DIR = ROOT / "evals" / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "evals/extraction.toml")
    parser.add_argument("--only", help="only runs whose name contains this text")
    parser.add_argument("--mistakes", action="store_true", help="list wrong fields")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    load_dotenv()  # api_key_env names are looked up in .env too
    samples = load_samples(SAMPLES_DIR / "vacancies", SAMPLES_DIR / "expected")
    configs = [
        c for c in load_configs(args.config) if not args.only or args.only in c.name
    ]
    print(f"{len(samples)} samples x {len(configs)} runs\n")

    results: list[RunResult] = []
    try:
        for config in configs:
            print(f"running {config.name} ...", flush=True)
            with LLMClient(config.settings()) as client:
                client.chat([{"role": "user", "content": "hi"}])  # warm-up, not scored
                results.append(run_eval(config, samples, client))
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach the LLM server: {err}")
        return 1

    print_summary(results)
    print_per_field(results)
    if args.mistakes:
        print_mistakes(results)
    if not args.no_save:
        print(f"\nsaved: {save(results).relative_to(ROOT)}")
    return 0


def print_summary(results: list[RunResult]) -> None:
    print(
        f"\n{'#':<3}{'run':<24} {'acc':>5} {'halluc':>6} {'missed':>6} {'wrong':>5} "
        f"{'skillF1':>7} {'langF1':>6} {'fail':>4} {'tries':>5} "
        f"{'out tok':>7} {'sec':>6} {'$/1k vac':>9}"
    )
    for i, r in enumerate(results, 1):
        tries = sum(s.attempts for s in r.samples)
        tokens = sum(s.output_tokens for s in r.samples)
        seconds = sum(s.latency_s for s in r.samples)
        halluc, missed, wrong = (
            r.total(o) for o in ("hallucinated", "missed", "wrong")
        )
        print(
            f"#{i:<2}{r.config.name:<24} {r.accuracy:>5.0%} "
            f"{halluc:>6} {missed:>6} {wrong:>5} "
            f"{r.list_f1('skills'):>7.2f} {r.list_f1('languages_required'):>6.2f} "
            f"{r.failed:>4} {tries:>5} {tokens:>7} {seconds:>6.1f} "
            f"{format_usd(r.usd_per_1k_vacancies):>9}"
        )


def format_usd(value: float | None) -> str:
    return "-" if value is None else f"${value:.2f}"


def print_per_field(results: list[RunResult]) -> None:
    """Which fields are hard? Share of samples where the field was right, per run."""
    fields = (
        [s.field for s in results[0].scored[0].scalars] if results[0].scored else []
    )
    header = "".join(f"{'#' + str(i):>8}" for i in range(1, len(results) + 1))
    print(f"\n{'field (right / samples)':<24}{header}")
    for field in fields:
        row = f"{field:<24}"
        for r in results:
            outcomes = [
                sc.outcome for s in r.scored for sc in s.scalars if sc.field == field
            ]
            good = sum(o in ("correct", "correct_null") for o in outcomes)
            row += f"{good:>6}/{len(r.samples)}"
        print(row)


def print_mistakes(results: list[RunResult]) -> None:
    for r in results:
        print(f"\n--- mistakes: {r.config.name} ---")
        for s in r.samples:
            if s.error:
                print(f"{s.sample}: FAILED {s.error.splitlines()[0]}")
                continue
            for sc in s.score.scalars:
                if sc.outcome not in ("correct", "correct_null"):
                    print(
                        f"{s.sample[:24]:<24} {sc.field:<20} {sc.outcome:<12} "
                        f"expected={sc.expected!r} got={sc.predicted!r}"
                    )
            for ls in s.score.lists:
                if ls.f1 < 1:
                    print(
                        f"{s.sample[:24]:<24} {ls.field:<20} F1={ls.f1:.2f}       "
                        f"missing={ls.missing} extra={ls.extra}"
                    )


def save(results: list[RunResult]) -> Path:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    now = datetime.datetime.now()
    report = {
        "date": now.isoformat(timespec="seconds"),
        "git_commit": commit,  # which code and prompt produced these numbers
        "runs": [
            {
                "summary": {
                    "accuracy": r.accuracy,
                    "hallucinated": r.total("hallucinated"),
                    "missed": r.total("missed"),
                    "wrong": r.total("wrong"),
                    "skills_f1": r.list_f1("skills"),
                    "languages_f1": r.list_f1("languages_required"),
                    "failed": r.failed,
                    "cost_usd": r.cost_usd,
                    "usd_per_1k_vacancies": r.usd_per_1k_vacancies,
                },
                **r.model_dump(mode="json"),
            }
            for r in results
        ],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{now:%Y-%m-%d_%H%M}_extraction.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return path


if __name__ == "__main__":
    sys.exit(main())
