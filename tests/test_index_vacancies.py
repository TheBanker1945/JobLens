"""scripts/index_vacancies.py: one refusal fails one vacancy, not the run."""

import json
from types import SimpleNamespace

import httpx
import openai
from index_vacancies import STOP_AFTER_API_ERRORS, extract_batch
from test_extract import VALID

from joblens.extraction.store import DetailsStore
from joblens.llm.types import ChatResult, Usage
from joblens.sources.base import Vacancy

CONFIG = SimpleNamespace(mode="schema", model="fake")


class Flaky:
    """Answers like a provider, except for vacancies it has been told to refuse,
    which get the error the SDK raises once its own retries have run out."""

    def __init__(self, refuse):
        self.refuse = refuse

    def chat(self, messages, **_):
        if self.refuse(str(messages)):
            raise openai.APIConnectionError(
                request=httpx.Request("POST", "https://provider.invalid")
            )
        return ChatResult(
            content=json.dumps(VALID),
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            model="fake",
            latency_s=0.1,
            finish_reason="stop",
        )


def vacancies(*texts: str) -> list[Vacancy]:
    return [
        Vacancy(source="test", source_id=str(i), url="", title=text, text=text)
        for i, text in enumerate(texts)
    ]


def test_an_api_error_fails_one_vacancy_and_the_rest_are_saved(tmp_path):
    """The 2026-09-24 crash: one 503 left through the top of the script, and
    the sources after it and the embedding step never ran."""
    store = DetailsStore(tmp_path)
    todo = vacancies("one", "two", "REFUSE three", "four", "five")

    batch = extract_batch(
        todo, Flaky(lambda text: "REFUSE" in text), CONFIG, store, "test"
    )

    assert (batch.done, batch.failed, batch.api_errors) == (4, 1, 1)
    assert not batch.stopped
    assert set(store.load("test")) == {"test:0", "test:1", "test:3", "test:4"}


def test_a_provider_that_keeps_refusing_is_not_asked_about_every_vacancy(tmp_path):
    todo = vacancies(*(f"vacancy {i}" for i in range(60)))

    batch = extract_batch(
        todo, Flaky(lambda _: True), CONFIG, DetailsStore(tmp_path), "t"
    )

    assert batch.stopped
    assert STOP_AFTER_API_ERRORS <= batch.failed < len(todo)


def test_what_came_back_before_the_provider_stopped_is_kept(tmp_path):
    store = DetailsStore(tmp_path)
    todo = vacancies("fine", *(f"REFUSE {i}" for i in range(40)))

    extract_batch(todo, Flaky(lambda text: "REFUSE" in text), CONFIG, store, "test")

    assert set(store.load("test")) == {"test:0"}
