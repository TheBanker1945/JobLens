"""Per-provider differences on top of the shared OpenAI-compatible protocol.

"OpenAI-compatible" covers the basics (model, messages, temperature), but every
provider switches reasoning ("thinking") on and off in its own way. Those extra
request fields live in this table, so clients never need `if provider == ...`.
"""

import functools
import logging
from dataclasses import dataclass, field
from typing import Any

from joblens.config import LLMSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderProfile:
    thinking_off: dict[str, Any] = field(default_factory=dict)  # extra request fields
    thinking_on: dict[str, Any] = field(default_factory=dict)


# Only profiles verified with a real call. A wrong mapping fails silently (thinking
# stays on and costs tokens), so add a provider here only after testing it.
PROFILES: dict[str, ProviderProfile] = {
    # Verified 2026-09-18 on Ollama 0.34.2 with qwen3:8b
    "ollama": ProviderProfile(thinking_off={"reasoning_effort": "none"}),
}


def thinking_params(settings: LLMSettings) -> dict[str, Any]:
    """Extra request fields that apply the thinking switch for this provider."""
    profile = PROFILES.get(settings.provider.lower())
    if profile is None:
        _warn_unknown_provider(settings.provider)
        return {}  # send nothing: the provider's own default applies
    return dict(profile.thinking_on if settings.thinking else profile.thinking_off)


@functools.cache  # warn once per provider, not on every call
def _warn_unknown_provider(provider: str) -> None:
    logger.warning(
        "No verified profile for provider %r: LLM_THINKING is ignored and the "
        "provider's default applies. Add a profile in joblens/llm/providers.py.",
        provider,
    )
