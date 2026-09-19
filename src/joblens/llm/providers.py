"""Per-provider and per-model differences on top of the OpenAI-compatible protocol.

"OpenAI-compatible" covers the basics (model, messages, temperature), but switching
reasoning ("thinking") on and off differs per provider, and on Gemini even per model.
Those extra request fields live in this table, so clients never need `if provider`.
"""

import functools
import logging
from dataclasses import dataclass, field
from typing import Any

from joblens.config import LLMSettings

logger = logging.getLogger(__name__)


class ThinkingNotSupportedError(ValueError):
    """The requested thinking setting is impossible for this model."""


@dataclass(frozen=True)
class ProviderProfile:
    # Extra request fields per thinking setting. `thinking_off=None` means the model
    # always thinks: asking for "off" raises instead of silently running with it on.
    thinking_off: dict[str, Any] | None = field(default_factory=dict)
    thinking_on: dict[str, Any] = field(default_factory=dict)
    # False: the thinking fields above were never tested, so LLM_THINKING is ignored.
    thinking_verified: bool = True
    # Can the provider force output to match a JSON schema (response_format
    # type "json_schema")? If not, extraction falls back to prompt-only JSON.
    supports_json_schema: bool = False


# Only entries verified with a real call. A wrong mapping fails silently (thinking
# stays on and costs tokens), so add one only after testing it. Keys are a provider,
# or "provider/model" for an exact model; the exact model wins. Exact, not prefix:
# gemini-3.8-flash and a future gemini-3.8-flash-lite may behave differently.
PROFILES: dict[str, ProviderProfile] = {
    # Verified 2026-09-18 on Ollama 0.34.2 with qwen3:8b, incl. $ref enums.
    "ollama": ProviderProfile(
        thinking_off={"reasoning_effort": "none"},
        supports_json_schema=True,
    ),
    # Gemini OpenAI-compatible endpoint. Schema support verified 2026-09-19 with
    # VacancyDetails ($ref enums included). Thinking differs per model, so the base
    # entry leaves it unverified; each model below was tested on 2026-09-19.
    "gemini": ProviderProfile(thinking_verified=False, supports_json_schema=True),
    "gemini/gemini-3.8-flash": ProviderProfile(
        # "none" reduces thinking but does not remove it (~330 hidden tokens left);
        # "minimal" is rejected with HTTP 400.
        thinking_off={"reasoning_effort": "none"},
        supports_json_schema=True,
    ),
    "gemini/gemini-3.5-flash-lite": ProviderProfile(
        # Does not think by default (and not with "low"); "medium" switches it on.
        # "none" is rejected with HTTP 400.
        thinking_off={"reasoning_effort": "minimal"},
        thinking_on={"reasoning_effort": "medium"},
        supports_json_schema=True,
    ),
    "gemini/gemini-pro-latest": ProviderProfile(
        thinking_off=None,  # "only works in thinking mode"
        supports_json_schema=True,
    ),
}


def profile_for(settings: LLMSettings) -> ProviderProfile | None:
    provider = settings.provider.lower()
    return PROFILES.get(f"{provider}/{settings.model}") or PROFILES.get(provider)


def thinking_params(settings: LLMSettings) -> dict[str, Any]:
    """Extra request fields that apply the thinking switch for this model."""
    profile = profile_for(settings)
    if profile is None or not profile.thinking_verified:
        _warn_unverified(settings.provider, settings.model)
        return {}  # send nothing: the provider's own default applies
    if settings.thinking:
        return dict(profile.thinking_on)
    if profile.thinking_off is None:
        raise ThinkingNotSupportedError(
            f"{settings.provider}/{settings.model} always thinks; "
            "set thinking to true for this model."
        )
    return dict(profile.thinking_off)


def supports_json_schema(settings: LLMSettings) -> bool:
    """Unknown providers: assume no, so extraction uses the safe prompt-only mode."""
    profile = profile_for(settings)
    return profile.supports_json_schema if profile else False


@functools.cache  # warn once per model, not on every call
def _warn_unverified(provider: str, model: str) -> None:
    logger.warning(
        "No verified thinking switch for %s/%s: LLM_THINKING is ignored and the "
        "provider's default applies. Add a profile in joblens/llm/providers.py.",
        provider,
        model,
    )
