"""The providers a person can bring their own key for, and nothing else (7.6).

Every one speaks the OpenAI-compatible protocol the LLM layer already uses, so
"bring your own AI" is only a different `LLMSettings`: this table supplies the
address, the person supplies a model id and a key.

**A fixed list, never a typed address.** A hosted server that sends requests to
whatever URL a user enters can be pointed at its own insides -- a cloud
metadata service, a database on the private network (SSRF). So a person picks
a provider by name and the address comes from here. The local ones (Ollama, LM
Studio) are offered only when JobLens itself runs on the person's machine: a
hosted server's "localhost" is the server, not them.

**A model is suggested only where it was measured.** gemini-3.8-flash is
JobLens's own default, chosen by the extraction eval (1.5) and behind every
judge eval since; qwen3:8b is the local fallback measured beside it. For the
rest, the person types the exact id from the provider's own list, and the test
call on saving proves it exists. JobLens does not guess model names.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    provider: str  # the name providers.py and pricing.py look models up under
    label: str
    base_url: str
    models_url: str  # where the provider lists its exact model ids
    suggested: str | None = None  # a measured model id, or None
    local: bool = False  # only reachable when JobLens runs on your own machine


PRESETS: dict[str, Preset] = {
    one.provider: one
    for one in (
        Preset(
            "gemini",
            "Google Gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "https://ai.google.dev/gemini-api/docs/models",
            suggested="gemini-3.8-flash",
        ),
        Preset(
            "openai",
            "OpenAI",
            "https://api.openai.com/v1",
            "https://platform.openai.com/docs/models",
        ),
        Preset(
            "anthropic",
            "Anthropic (Claude)",
            "https://api.anthropic.com/v1/",
            "https://docs.anthropic.com/en/docs/about-claude/models",
        ),
        Preset(
            "openrouter",
            "OpenRouter",
            "https://openrouter.ai/api/v1",
            "https://openrouter.ai/models",
        ),
        Preset(
            "deepseek",
            "DeepSeek",
            "https://api.deepseek.com/v1",
            "https://api-docs.deepseek.com/quick_start/pricing",
        ),
        Preset(
            "ollama",
            "Ollama (on your machine)",
            "http://localhost:11434/v1",
            "https://ollama.com/library",
            suggested="qwen3:8b",
            local=True,
        ),
        Preset(
            "lmstudio",
            "LM Studio (on your machine)",
            "http://localhost:1234/v1",
            "https://lmstudio.ai/models",
            local=True,
        ),
    )
}

# What JobLens has measured, in the person's words on a settings page. A model
# that is not here works -- every quote is still checked in code -- but nobody
# has put a number on how well.
MEASURED: dict[str, str] = {
    "gemini/gemini-3.8-flash": (
        "JobLens's default: chosen by the extraction eval, and used for every "
        "judge measurement."
    ),
    "ollama/qwen3:8b": (
        "The local fallback: measured beside Gemini, and weaker at reading CVs."
    ),
}


def available(allow_local: bool) -> list[Preset]:
    return [one for one in PRESETS.values() if allow_local or not one.local]
