"""Send one question to the configured LLM and show what came back.

Usage:
    uv run python scripts/raw_call.py "Wat is een vacature?"
    uv run python scripts/raw_call.py "Wat is een vacature?" --think
"""

import argparse
import sys

import httpx

from joblens.config import load_llm_settings
from joblens.llm.raw_client import chat

SYSTEM_PROMPT = (
    "Je bent een behulpzame assistent voor de Nederlandse arbeidsmarkt. "
    "Antwoord kort en in het Nederlands."
)
REASONING_PREVIEW_CHARS = 500


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt", nargs="?", default="Wat is een vacature?")
    parser.add_argument("--think", action="store_true", help="switch thinking on")
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    settings = load_llm_settings()
    if args.think:
        settings = settings.model_copy(update={"thinking": True})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": args.prompt},
    ]

    try:
        result = chat(messages, settings, temperature=args.temperature)
    except httpx.ConnectError:
        print(f"Cannot reach {settings.base_url}. Is the LLM server running?")
        return 1
    except httpx.HTTPStatusError as err:
        print(f"HTTP {err.response.status_code} from provider: {err.response.text}")
        return 1

    thinking = "on" if settings.thinking else "off"
    print(f"model:    {result.model}  (thinking: {thinking})")
    print(f"latency:  {result.latency_s:.2f} s")
    if result.usage:
        u = result.usage
        print(
            f"tokens:   in {u.prompt_tokens} · out {u.output_tokens}"
            f" · total {u.total_tokens}"
        )
    if result.reasoning:
        preview = result.reasoning[:REASONING_PREVIEW_CHARS]
        more = "..." if len(result.reasoning) > REASONING_PREVIEW_CHARS else ""
        print(f"\n--- reasoning ({len(result.reasoning)} chars) ---\n{preview}{more}")
    print(f"\n--- answer ---\n{result.content}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
