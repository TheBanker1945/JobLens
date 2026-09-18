"""Extraction tests with a fake ChatClient: scripted replies, no model."""

import json

import pytest

from joblens.config import LLMSettings
from joblens.extraction.extract import (
    ExtractionError,
    default_mode,
    extract_vacancy,
)
from joblens.llm.types import ChatResult, Usage

VALID = {
    "title": "Junior Data Analist",
    "company": "Bakkerij De Vries B.V.",
    "city": "Utrecht",
    "work_mode": "hybrid",
    "hours_min": 32,
    "hours_max": 36,
    "salary_min": 3200,
    "salary_max": 3800,
    "salary_period": "month",
    "salary_note": None,
    "education_level": "hbo",
    "experience_years_min": 0,
    "skills": ["SQL", "Power BI"],
    "languages_required": ["Dutch"],
    "contract_type": "temporary",
}
VACANCY_TEXT = "Junior Data Analist bij Bakkerij De Vries in Utrecht..."


class FakeClient:
    """Satisfies the ChatClient interface; returns scripted replies in order."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, *, temperature=0.0, response_format=None):
        self.calls.append({"messages": list(messages), "format": response_format})
        return ChatResult(
            content=self.replies.pop(0),
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            model="fake",
            latency_s=0.5,
        )


def test_schema_mode_sends_schema_and_parses_valid_reply():
    client = FakeClient(json.dumps(VALID))

    result = extract_vacancy(VACANCY_TEXT, client, mode="schema")

    assert result.details.title == "Junior Data Analist"
    assert result.attempts == 1
    sent_format = client.calls[0]["format"]
    assert sent_format["type"] == "json_schema"
    assert "salary_period" in sent_format["json_schema"]["schema"]["properties"]


def test_prompt_mode_sends_no_format_but_schema_is_in_prompt():
    client = FakeClient(json.dumps(VALID))

    extract_vacancy(VACANCY_TEXT, client, mode="prompt")

    call = client.calls[0]
    assert call["format"] is None
    assert '"salary_period"' in call["messages"][0]["content"]  # system prompt
    assert call["messages"][1]["content"] == VACANCY_TEXT


def test_json_wrapped_in_text_and_fences_is_accepted():
    reply = f"Here is the data:\n```json\n{json.dumps(VALID)}\n```\nGood luck!"

    result = extract_vacancy(VACANCY_TEXT, FakeClient(reply), mode="prompt")

    assert result.details.city == "Utrecht"


def test_invalid_reply_is_repaired_with_error_feedback():
    wrong = json.dumps(VALID | {"salary_min": 5000})  # min > max
    client = FakeClient(wrong, json.dumps(VALID))

    result = extract_vacancy(VACANCY_TEXT, client)

    assert result.attempts == 2
    repair_messages = client.calls[1]["messages"]
    assert repair_messages[-2] == {"role": "assistant", "content": wrong}
    assert "salary_min is greater than salary_max" in repair_messages[-1]["content"]


def test_broken_json_is_repaired():
    truncated = json.dumps(VALID)[:80]
    client = FakeClient(truncated, json.dumps(VALID))

    assert extract_vacancy(VACANCY_TEXT, client).attempts == 2


def test_gives_up_after_max_attempts():
    client = FakeClient("not json", "still not json")

    with pytest.raises(ExtractionError, match="after 2 attempts") as info:
        extract_vacancy(VACANCY_TEXT, client, max_attempts=2)
    assert len(info.value.attempts) == 2


def test_tokens_and_latency_are_summed_over_attempts():
    client = FakeClient("not json", json.dumps(VALID))

    result = extract_vacancy(VACANCY_TEXT, client)

    assert result.prompt_tokens == 200
    assert result.completion_tokens == 100
    assert result.latency_s == 1.0


@pytest.mark.parametrize(("provider", "mode"), [("ollama", "schema"), ("x", "prompt")])
def test_default_mode_follows_provider_capability(provider, mode):
    settings = LLMSettings(
        provider=provider, base_url="http://h", api_key="k", model="m"
    )

    assert default_mode(settings) == mode
