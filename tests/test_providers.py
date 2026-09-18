import logging

from joblens.config import LLMSettings
from joblens.llm.providers import supports_json_schema, thinking_params


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
