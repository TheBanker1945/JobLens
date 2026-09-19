from joblens.llm.types import Usage


def test_openai_and_ollama_style_usage_has_no_hidden_tokens():
    # Reasoning, if any, is already inside completion_tokens.
    usage = Usage.from_api(
        {"prompt_tokens": 48, "completion_tokens": 207, "total_tokens": 255}
    )

    assert usage.hidden_reasoning_tokens == 0
    assert usage.output_tokens == 207


def test_gemini_style_usage_reveals_hidden_reasoning():
    usage = Usage.from_api(
        {"prompt_tokens": 45, "completion_tokens": 13, "total_tokens": 389}
    )

    assert usage.hidden_reasoning_tokens == 331
    assert usage.output_tokens == 344


def test_missing_total_is_computed():
    usage = Usage.from_api({"prompt_tokens": 10, "completion_tokens": 5})

    assert usage.total_tokens == 15
    assert usage.hidden_reasoning_tokens == 0
