"""A per-minute quota is waited out, not failed on (7.8.5). Fake server in httpx2,
as the openai SDK uses.

Measured on the first cloud run: Gemini counts every text in a batch against
its per-minute limit, and a backlog of 480 new vacancies was refused with a
429 halfway through. The client now waits a minute and sends the batch again,
a few times, before it gives up.
"""

import httpx2
import pytest
from openai import DefaultHttpxClient, RateLimitError

from joblens.config import LLMSettings
from joblens.embeddings.client import (
    RATE_LIMIT_WAIT_S,
    RATE_LIMIT_WAITS,
    EmbeddingClient,
)

SETTINGS = LLMSettings(
    provider="fake", base_url="http://fake.test/v1", api_key="k", model="m"
)
QUOTA = {"error": {"code": 429, "message": "You exceeded your current quota"}}


def client_answering(*answers):
    """A fake /embeddings that gives these status codes in turn (200: two numbers)."""
    calls = []

    def handler(request):
        status = answers[min(len(calls), len(answers) - 1)]
        calls.append(status)
        if status == 429:
            return httpx2.Response(429, json=QUOTA)
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "m",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.5, 0.25]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    waits = []
    client = EmbeddingClient(
        SETTINGS,
        max_retries=0,
        sleep=waits.append,
        http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return client, calls, waits


def test_a_quota_refusal_is_waited_out_and_the_batch_sent_again():
    client, calls, waits = client_answering(429, 200)

    vectors = client.embed_documents(["a vacancy"])

    assert vectors == [[0.5, 0.25]]
    assert calls == [429, 200] and waits == [RATE_LIMIT_WAIT_S]


def test_a_quota_that_keeps_refusing_still_fails_in_the_end():
    client, calls, waits = client_answering(429)

    with pytest.raises(RateLimitError):
        client.embed_documents(["a vacancy"])

    assert len(calls) == RATE_LIMIT_WAITS + 1 and len(waits) == RATE_LIMIT_WAITS
