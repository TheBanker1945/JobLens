import pytest

from joblens.config import load_llm_settings

OLLAMA_ENV = {
    "LLM_PROVIDER": "ollama",
    "LLM_BASE_URL": "http://localhost:11434/v1/",
    "LLM_API_KEY": "ollama",
    "LLM_MODEL": "qwen3:8b",
}


def test_loads_settings_from_env():
    settings = load_llm_settings(OLLAMA_ENV)

    assert settings.provider == "ollama"
    assert settings.base_url == "http://localhost:11434/v1"  # trailing slash removed
    assert settings.model == "qwen3:8b"
    assert settings.thinking is False  # off by default


def test_thinking_can_be_switched_on():
    settings = load_llm_settings({**OLLAMA_ENV, "LLM_THINKING": "true"})

    assert settings.thinking is True


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_empty_var_names_the_variable(value):
    env = {**OLLAMA_ENV, "LLM_MODEL": value}
    env = {k: v for k, v in env.items() if v is not None}

    with pytest.raises(ValueError, match="LLM_MODEL"):
        load_llm_settings(env)


def test_rejects_base_url_without_scheme():
    with pytest.raises(ValueError, match="http"):
        load_llm_settings({**OLLAMA_ENV, "LLM_BASE_URL": "localhost:11434/v1"})


def test_api_key_is_hidden_from_repr():
    settings = load_llm_settings({**OLLAMA_ENV, "LLM_API_KEY": "sk-secret"})

    assert "sk-secret" not in repr(settings)


def test_prefix_selects_settings_for_another_task():
    env = OLLAMA_ENV | {
        "EMBED_PROVIDER": "ollama",
        "EMBED_BASE_URL": "http://localhost:11434/v1",
        "EMBED_API_KEY": "ollama",
        "EMBED_MODEL": "qwen3-embedding:0.6b",
    }

    assert load_llm_settings(env, prefix="EMBED").model == "qwen3-embedding:0.6b"
    assert load_llm_settings(env).model == "qwen3:8b"  # LLM_ is still the default


def test_missing_prefixed_var_names_the_variable():
    with pytest.raises(ValueError, match="EMBED_MODEL"):
        load_llm_settings(OLLAMA_ENV, prefix="EMBED")
