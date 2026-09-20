"""Extract all sample vacancies once and store the result for other tools to use.

The retrieval eval (2.2) embeds a structured summary built from these files, so it
needs no extraction API calls and gives the same result every run.

Usage:
    uv run python scripts/extract_samples.py                    # default run
    uv run python scripts/extract_samples.py --run "qwen3:8b"   # another eval run
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from joblens.evals.runner import load_configs
from joblens.extraction.extract import ExtractionError, extract_vacancy
from joblens.llm.client import LLMClient

ROOT = Path(__file__).parent.parent
SAMPLES = ROOT / "data" / "samples" / "vacancies"
OUT = ROOT / "data" / "samples" / "extracted"
DEFAULT_RUN = "gemini-3.8-flash"  # the default for vacancy extraction (CLAUDE.md)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run", default=DEFAULT_RUN, help="run name in the eval config"
    )
    args = parser.parse_args()

    load_dotenv()
    configs = {c.name: c for c in load_configs(ROOT / "evals" / "extraction.toml")}
    if args.run not in configs:
        print(f"Unknown run {args.run!r}. Available: {', '.join(configs)}")
        return 1

    config = configs[args.run]
    OUT.mkdir(parents=True, exist_ok=True)
    with LLMClient(config.settings()) as client:
        for path in sorted(SAMPLES.glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            try:
                result = extract_vacancy(text, client, mode=config.mode)
            except ExtractionError as err:
                print(f"{path.stem}: FAILED {err}")
                return 1
            payload = {
                "model": config.model,
                "details": result.details.model_dump(mode="json"),
            }
            (OUT / f"{path.stem}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"{path.stem:<32} {result.details.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
