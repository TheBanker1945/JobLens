"""Send the same question through the raw httpx client and the SDK client, side by side.

Usage:
    uv run python scripts/compare_clients.py
    uv run python scripts/compare_clients.py "Noem drie IT-banen" --runs 5 --think
"""

import argparse
import statistics
import sys

import httpx
import openai

from joblens.config import load_llm_settings
from joblens.llm import raw_client
from joblens.llm.client import LLMClient

SYSTEM_PROMPT = (
    "Je bent een behulpzame assistent voor de Nederlandse arbeidsmarkt. "
    "Antwoord kort en in het Nederlands."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt", nargs="?", default="Wat is een vacature?")
    parser.add_argument("--runs", type=int, default=3, help="calls per client")
    parser.add_argument("--think", action="store_true", help="switch thinking on")
    args = parser.parse_args()

    settings = load_llm_settings()
    if args.think:
        settings = settings.model_copy(update={"thinking": True})
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": args.prompt},
    ]

    try:
        with LLMClient(settings) as sdk, httpx.Client(timeout=120) as http:
            clients = {
                "raw (httpx)": lambda: raw_client.chat(messages, settings, client=http),
                "sdk (openai)": lambda: sdk.chat(messages),
            }
            print(f"model: {settings.model}  thinking: {settings.thinking}")
            print("warm-up call (loads the model, not measured)...\n")
            clients["sdk (openai)"]()

            results = {
                name: [call() for _ in range(args.runs)]
                for name, call in clients.items()
            }
    except (httpx.ConnectError, openai.APIConnectionError):
        print(f"Cannot reach {settings.base_url}. Is the LLM server running?")
        return 1

    print(f"{'client':<14} {'median s':>9} {'out tokens':>11}  answer")
    for name, runs in results.items():
        median = statistics.median(r.latency_s for r in runs)
        tokens = runs[0].usage.completion_tokens if runs[0].usage else "?"
        answer = runs[0].content.replace("\n", " ")[:60]
        print(f"{name:<14} {median:>9.2f} {tokens:>11}  {answer}")

    answers = {r.content for runs in results.values() for r in runs}
    print(f"\ndistinct answers across all {2 * args.runs} calls: {len(answers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
