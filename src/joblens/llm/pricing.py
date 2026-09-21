"""What a call costs, per model.

The brief for CV matching asks to see roughly what a run costs, in money and in
time, and a token count alone does not say that. The eval configs
(evals/*.toml) already carry prices per run, but those describe an experiment;
this table describes the models the app itself uses, so `scripts/read_cv.py` and
`scripts/match_cv.py` can print a number without an eval being involved.

Rules, the same as for the thinking profiles in providers.py:
- Exact model IDs, never "-latest" aliases: an alias points at a new model
  tomorrow and the price silently stops being true.
- Every entry says where the number came from and when it was checked, because a
  price is a fact about a day, not about a model.
- A model that is not in here has no price. That prints as "unknown", never as 0:
  a wrong cost is worse than no cost.
"""

from dataclasses import dataclass

from joblens.config import LLMSettings


@dataclass(frozen=True)
class Price:
    """USD per 1 million tokens. Output includes reasoning tokens."""

    usd_per_m_input: float
    usd_per_m_output: float
    source: str
    checked: str  # ISO date the price was last read from `source`

    def usd(self, prompt_tokens: int, output_tokens: int) -> float:
        return (
            prompt_tokens * self.usd_per_m_input + output_tokens * self.usd_per_m_output
        ) / 1e6


GEMINI_PRICING = "https://ai.google.dev/gemini-api/docs/pricing"

# Keyed by provider, or "provider/model" for one exact model; the exact model
# wins. A local server costs nothing per token, which is a property of the
# provider rather than of the model, so it is keyed by provider alone.
PRICES: dict[str, Price] = {
    "ollama": Price(0.0, 0.0, "local model: no API cost", "2026-09-21"),
    "lmstudio": Price(0.0, 0.0, "local model: no API cost", "2026-09-21"),
    # Introductory rate, valid through 2026-12-31 (see CLAUDE.md).
    "gemini/gemini-3.8-flash": Price(0.75, 3.75, GEMINI_PRICING, "2026-09-19"),
    "gemini/gemini-3.5-flash-lite": Price(0.30, 2.50, GEMINI_PRICING, "2026-09-19"),
    "gemini/gemini-embedding-2": Price(0.15, 0.0, GEMINI_PRICING, "2026-09-21"),
    "gemini/gemini-embedding-001": Price(0.15, 0.0, GEMINI_PRICING, "2026-09-21"),
}


def price_for(settings: LLMSettings) -> Price | None:
    provider = settings.provider.lower()
    return PRICES.get(f"{provider}/{settings.model}") or PRICES.get(provider)


def cost_usd(
    settings: LLMSettings, prompt_tokens: int, output_tokens: int
) -> float | None:
    """None when this model has no price: unknown is not the same as free."""
    price = price_for(settings)
    return None if price is None else price.usd(prompt_tokens, output_tokens)


def format_cost(usd: float | None) -> str:
    """Small amounts in cents, because a CV run costs a few of them."""
    if usd is None:
        return "unknown (no price for this model in llm/pricing.py)"
    if usd == 0:
        return "$0 (local model)"
    if usd < 0.01:
        return f"{usd * 100:.2f} cent"
    return f"${usd:.3f}"
