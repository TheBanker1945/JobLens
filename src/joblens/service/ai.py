"""Whose model reads a person's CV and judges their matches: theirs, or ours (7.6).

A person without a key of their own uses JobLens's model (the operator's
Gemini key), within a budget if they are a tester (service/budget.py). A
person who brings a key uses their own model, as much as they like. Either
way only the `cv` model changes: the embedding model stays JobLens's, because
a CV is only comparable with vacancies embedded by the same model (7.1).

**What bringing a key means, step by step.** Pick a provider from a fixed list
(llm/presets.py), type the exact model id, paste the key. One tiny call is made
with those settings before anything is stored -- a wrong key, a model that does
not exist, a model that cannot switch thinking off, are refused there and then,
not in the middle of the next match. The key is stored encrypted
(joblens/vault.py) and never shown again; the page gets its last four
characters.
"""

from collections.abc import Callable
from contextlib import closing
from datetime import datetime

from pydantic import BaseModel

from joblens.config import LLMSettings
from joblens.llm.client import LLMClient
from joblens.llm.presets import MEASURED, PRESETS
from joblens.llm.providers import ThinkingNotSupportedError
from joblens.service.errors import ProviderRefused, ProviderUnreachable, ServiceError
from joblens.service.matching import ChatFactory, Models, provider_errors
from joblens.storage import PostgresStore, ProviderKey
from joblens.vault import Vault

OPERATOR, OWN = "operator", "own"


class OwnKeysOff(ServiceError):
    """This server has no secret to encrypt keys with, so it takes none."""


class AIChoice(BaseModel):
    """What a settings page shows: whose model, which one, never the key."""

    using: str  # "own" or "joblens"
    provider: str
    model: str
    thinking: bool
    key_hint: str | None = None  # "...a1b2" for an own key
    verified_at: datetime | None = None
    measured: str | None = None  # what JobLens measured about this model, if anything


def models_for(
    store: PostgresStore, operator: Models, vault: Vault | None
) -> tuple[Models, str]:
    """The models for this person's next paid call, and who pays for it."""
    own = store.provider_key()
    if own is None or vault is None:
        return operator, OPERATOR
    settings = LLMSettings(
        provider=own.provider,
        base_url=PRESETS[own.provider].base_url,
        api_key=vault.unlock(own.key_secret),
        model=own.model,
        thinking=own.thinking,
    )
    return Models(cv=settings, embed=operator.embed), OWN


def choice(store: PostgresStore, operator: Models) -> AIChoice:
    own = store.provider_key()
    return _own(own) if own else _joblens(operator)


def _own(own: ProviderKey) -> AIChoice:
    return AIChoice(
        using="own",
        provider=own.provider,
        model=own.model,
        thinking=own.thinking,
        key_hint=f"...{own.key_hint}",
        verified_at=own.verified_at,
        measured=MEASURED.get(f"{own.provider}/{own.model}"),
    )


def _joblens(operator: Models) -> AIChoice:
    cv = operator.cv
    return AIChoice(
        using="joblens",
        provider=cv.provider,
        model=cv.model,
        thinking=cv.thinking,
        measured=MEASURED.get(f"{cv.provider}/{cv.model}"),
    )


def check_provider(settings: LLMSettings, chat: ChatFactory = LLMClient) -> None:
    """One tiny call with these settings. Raises a ServiceError if it fails."""
    try:
        with provider_errors(), closing(chat(settings)) as client:
            client.chat(
                [{"role": "user", "content": "Reply with the single word OK."}],
                max_tokens=64,
            )
    except ThinkingNotSupportedError as err:
        raise ValueError(f"{err} Tick 'thinking' for this model.") from err


def bring_own_key(
    store: PostgresStore,
    vault: Vault | None,
    *,
    provider: str,
    model: str,
    api_key: str,
    thinking: bool = False,
    allow_local: bool = False,
    check: Callable[[LLMSettings], None] = check_provider,
) -> AIChoice:
    """Test the settings with one call, then keep them, the key encrypted."""
    if vault is None:
        raise OwnKeysOff(
            "This server does not take own keys: it has no JOBLENS_SECRET_KEY "
            "to encrypt them with."
        )
    preset = PRESETS.get(provider)
    if preset is None or (preset.local and not allow_local):
        raise ValueError(f"not a provider this server offers: {provider!r}")
    model, api_key = model.strip(), api_key.strip()
    if not model or "latest" in model:
        # CLAUDE.md: exact model ids, never an alias that changes under you.
        raise ValueError("give the exact model id, not a '-latest' alias")
    if len(api_key) < 8 and not preset.local:
        raise ValueError("that does not look like an API key")
    settings = LLMSettings(
        provider=provider,
        base_url=preset.base_url,
        api_key=api_key or "local",  # Ollama ignores it; the SDK needs one
        model=model,
        thinking=thinking,
    )
    try:
        check(settings)
    except ProviderRefused as err:
        if err.busy:
            raise  # the provider is busy, not the key wrong: try again later
        # Measured 2026-09-25: Gemini answers a bad key with 400 "Please pass a
        # valid API key" and an unknown model with 404. Both are this person's
        # settings, so they get the provider's own sentence and a 400.
        reason = str(err).splitlines()[-1]
        raise ValueError(
            f"{preset.label} refused this key or model ({err.status_code}): "
            f"{reason[:240]} Nothing was saved."
        ) from err
    except ProviderUnreachable as err:
        raise ValueError(f"{err} Nothing was saved.") from err
    saved = store.save_provider_key(
        provider=provider,
        model=model,
        thinking=thinking,
        key_secret=vault.lock(api_key or "local"),
        key_hint=(api_key or "local")[-4:],
    )
    return _own(saved)
