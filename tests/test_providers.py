import logging

import pytest

from joblens.config import LLMSettings
from joblens.llm.providers import (
    ThinkingNotSupportedError,
    supports_json_schema,
    thinking_params,
)


def make_settings(provider="ollama", thinking=False):
    return LLMSettings(
        provider=provider,
        base_url="http://localhost:11434/v1",
        api_key="key",
        model="qwen3:8b",
        thinking=thinking,
    )


def test_ollama_thinking_off_sends_reasoning_effort_none():
    assert thinking_params(make_settings()) == {"reasoning_effort": "none"}


def test_ollama_thinking_on_sends_nothing_extra():
    assert thinking_params(make_settings(thinking=True)) == {}


def test_provider_name_is_case_insensitive():
    assert thinking_params(make_settings(provider="Ollama")) == {
        "reasoning_effort": "none"
    }


def test_unknown_provider_sends_nothing_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        params = thinking_params(make_settings(provider="some-new-provider"))

    assert params == {}
    assert "some-new-provider" in caplog.text


def test_returned_params_are_a_copy():
    thinking_params(make_settings())["reasoning_effort"] = "high"

    assert thinking_params(make_settings()) == {"reasoning_effort": "none"}


def test_json_schema_support_is_known_for_ollama_only():
    assert supports_json_schema(make_settings()) is True
    assert supports_json_schema(make_settings(provider="some-new-provider")) is False


def gemini(model, thinking=False):
    return LLMSettings(
        provider="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="key",
        model=model,
        thinking=thinking,
    )


@pytest.mark.parametrize(
    ("model", "thinking", "params"),
    [
        ("gemini-3.8-flash", False, {"reasoning_effort": "none"}),
        ("gemini-3.8-flash", True, {}),
        ("gemini-3.5-flash-lite", False, {"reasoning_effort": "minimal"}),
        ("gemini-3.5-flash-lite", True, {"reasoning_effort": "medium"}),
        ("gemini-3.1-pro-preview", True, {}),
    ],
)
def test_gemini_thinking_is_chosen_per_model(model, thinking, params):
    assert thinking_params(gemini(model, thinking)) == params


def test_model_that_always_thinks_refuses_thinking_off():
    with pytest.raises(ThinkingNotSupportedError, match="always thinks"):
        thinking_params(gemini("gemini-3.1-pro-preview", thinking=False))


def test_unverified_gemini_model_sends_nothing_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        params = thinking_params(gemini("gemini-9-experimental"))

    assert params == {}
    assert "gemini/gemini-9-experimental" in caplog.text


def test_exact_model_match_only():
    # A similar name must not inherit a profile verified for another model.
    assert thinking_params(gemini("gemini-3.8-flash-lite")) == {}


def test_unverified_gemini_model_still_gets_schema_support():
    assert supports_json_schema(gemini("gemini-9-experimental")) is True
