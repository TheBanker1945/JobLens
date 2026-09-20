"""EmbeddingClient against a fake server (httpx2, like the openai SDK uses)."""

import json

import httpx2
import openai

from joblens.config import LLMSettings
from joblens.embeddings.client import EmbeddingClient

SETTINGS = LLMSettings(
    provider="ollama",
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="qwen3-embedding:0.6b",
)


def fake_client(seen: list, reverse: bool = False, **kwargs) -> EmbeddingClient:
    """Answers each input with a vector [i, len(text)]; optionally out of order."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        inputs = seen[-1]["input"]
        data = [
            {"object": "embedding", "index": i, "embedding": [float(i), len(t)]}
            for i, t in enumerate(inputs)
        ]
        if reverse:
            data.reverse()
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": SETTINGS.model,
                "data": data,
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    return EmbeddingClient(SETTINGS, http_client=http, **kwargs)


def test_documents_are_embedded_without_instruction():
    seen = []

    vectors = fake_client(seen).embed_documents(["a", "bb"])

    assert seen[0]["model"] == "qwen3-embedding:0.6b"
    assert seen[0]["input"] == ["a", "bb"]  # the SDK also asks for base64 transport
    assert vectors == [[0.0, 1.0], [1.0, 2.0]]


def test_query_gets_the_models_instruction():
    seen = []

    fake_client(seen).embed_query("python amsterdam", task="Find jobs")

    assert seen[0]["input"] == ["Instruct: Find jobs\nQuery: python amsterdam"]


def test_unknown_model_embeds_query_as_is():
    seen = []
    client = fake_client(seen)
    client.settings = SETTINGS.model_copy(update={"model": "other-embedder"})

    client.embed_query("python amsterdam")

    assert seen[0]["input"] == ["python amsterdam"]


def test_documents_are_sent_in_batches():
    seen = []

    vectors = fake_client(seen, batch_size=2).embed_documents(["a", "b", "c"])

    assert [len(body["input"]) for body in seen] == [2, 1]
    assert len(vectors) == 3


def test_vectors_keep_input_order_even_if_server_reorders():
    vectors = fake_client([], reverse=True).embed_documents(["a", "bb", "ccc"])

    assert [v[1] for v in vectors] == [1, 2, 3]  # lengths of a, bb, ccc


def test_works_when_the_server_omits_index(monkeypatch):
    """Gemini leaves 'index' empty and relies on the order of the items."""
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "gemini-embedding-2",
                "data": [
                    {"object": "embedding", "index": None, "embedding": [1.0, 0.0]},
                    {"object": "embedding", "index": None, "embedding": [0.0, 1.0]},
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    client = EmbeddingClient(SETTINGS, http_client=http)

    assert client.embed_documents(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
