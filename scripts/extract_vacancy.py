"""Extract VacancyDetails from sample vacancies, in schema and/or prompt mode.

Usage:
    uv run python scripts/extract_vacancy.py                  # all samples, both modes
    uv run python scripts/extract_vacancy.py data/samples/vacancies/03_*.txt --show
    uv run python scripts/extract_vacancy.py --mode prompt
"""

import argparse
import sys
from pathlib import Path

import httpx
import openai

from joblens.config import load_llm_settings
from joblens.extraction.extract import ExtractionError, default_mode, extract_vacancy
from joblens.llm.client import LLMClient

SAMPLES = Path(__file__).parent.parent / "data" / "samples" / "vacancies"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="default: all samples")
    parser.add_argument("--mode", choices=["schema", "prompt", "both"], default="both")
    parser.add_argument("--show", action="store_true", help="print the full JSON")
    args = parser.parse_args()

    files = args.files or sorted(SAMPLES.glob("*.txt"))
    # Thinking off for extraction: faster, fewer tokens, same structure.
    settings = load_llm_settings().model_copy(update={"thinking": False})
    modes = ["schema", "prompt"] if args.mode == "both" else [args.mode]
    if "schema" in modes and default_mode(settings) == "prompt":
        print(f"Note: {settings.provider} has no verified JSON-schema support.\n")

    print(f"model: {settings.model}\n")
    print(f"{'sample':<36} {'mode':<7} {'tries':>5} {'out tok':>8} {'sec':>6}  result")
    try:
        with LLMClient(settings) as client:
            for path in files:
                text = path.read_text(encoding="utf-8")
                outputs = {}
                for mode in modes:
                    outputs[mode] = run(path, text, client, mode, args.show)
                if len(outputs) == 2 and all(outputs.values()):
                    print_differences(outputs["schema"], outputs["prompt"])
    except (httpx.ConnectError, openai.APIConnectionError):
        print(f"Cannot reach {settings.base_url}. Is the LLM server running?")
        return 1
    return 0


def run(path: Path, text: str, client, mode: str, show: bool) -> dict | None:
    try:
        result = extract_vacancy(text, client, mode=mode)
    except ExtractionError as err:
        first_error = str(err).splitlines()[1] if "\n" in str(err) else str(err)
        print(
            f"{path.stem:<36} {mode:<7} {len(err.attempts):>5} {'':>8} {'':>6}  "
            f"FAILED {first_error}"
        )
        return None

    d = result.details
    print(
        f"{path.stem:<36} {mode:<7} {result.attempts:>5} "
        f"{result.completion_tokens:>8} {result.latency_s:>6.1f}  {d.title}"
    )
    if show:
        print(d.model_dump_json(indent=2))
    return d.model_dump(mode="json")


def print_differences(schema_out: dict, prompt_out: dict) -> None:
    diffs = [k for k in schema_out if schema_out[k] != prompt_out[k]]
    for key in diffs:
        print(
            f"{'':<36}   ≠ {key}: schema={schema_out[key]!r} prompt={prompt_out[key]!r}"
        )


if __name__ == "__main__":
    sys.exit(main())
