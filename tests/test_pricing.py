"""What a call costs. The rule being tested is mostly "say nothing rather than
something wrong": a model with no price prints as unknown, never as free."""

import pytest

from joblens.config import LLMSettings
from joblens.llm.pricing import cost_usd, format_cost, price_for


def settings(provider: str, model: str) -> LLMSettings:
    return LLMSettings(provider=provider, base_url="http://h", api_key="k", model=model)


def test_an_exact_model_beats_the_provider_entry():
    price = price_for(settings("gemini", "gemini-3.8-flash"))

    assert price is not None
    assert price.usd_per_m_input == 0.75


def test_a_provider_entry_covers_every_model_it_serves():
    """Ollama is free whichever model it is running."""
    assert cost_usd(settings("ollama", "qwen3:8b"), 10_000, 5_000) == 0.0


def test_an_unknown_model_has_no_price_rather_than_a_zero_one():
    assert cost_usd(settings("gemini", "gemini-99-turbo"), 1000, 1000) is None
    assert price_for(settings("nobody", "nothing")) is None


def test_the_cost_of_reading_one_cv():
    """A CV is about 2,400 tokens in and 800 out (measured on the sample CVs)."""
    usd = cost_usd(settings("gemini", "gemini-3.8-flash"), 2400, 800)

    assert usd == pytest.approx(0.0048, abs=0.0001)


@pytest.mark.parametrize(
    ("usd", "shown"),
    [
        (None, "unknown (no price for this model in llm/pricing.py)"),
        (0.0, "$0 (local model)"),
        (0.0048, "0.48 cent"),
        (0.71, "$0.710"),
    ],
)
def test_amounts_are_printed_at_the_size_they_are(usd, shown):
    assert format_cost(usd) == shown
