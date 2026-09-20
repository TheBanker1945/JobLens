from joblens.config import LLMSettings
from joblens.embeddings.client import EmbeddingClient
from joblens.embeddings.store import CachedEmbedder

SETTINGS = LLMSettings(
    provider="ollama",
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    model="qwen3-embedding:0.6b",
)


class CountingClient(EmbeddingClient):
    """Counts what is actually sent, without touching the network."""

    def __init__(self, settings=SETTINGS):
        self.settings = settings
        self.sent: list[str] = []

    def embed_documents(self, texts):
        self.sent += texts
        return [[float(len(t)), 1.0] for t in texts]


def test_second_call_comes_from_the_cache(tmp_path):
    client = CountingClient()
    cache = CachedEmbedder(client, tmp_path / "cache.json")

    first = cache.embed_documents(["a", "bb"])
    second = cache.embed_documents(["a", "bb"])

    assert first == second
    assert client.sent == ["a", "bb"]  # sent once
    assert (cache.hits, cache.misses) == (2, 2)


def test_only_missing_texts_are_sent(tmp_path):
    client = CountingClient()
    cache = CachedEmbedder(client, tmp_path / "cache.json")

    cache.embed_documents(["a"])
    cache.embed_documents(["a", "new"])

    assert client.sent == ["a", "new"]


def test_duplicates_are_embedded_once(tmp_path):
    client = CountingClient()

    vectors = CachedEmbedder(client, tmp_path / "c.json").embed_documents(["x", "x"])

    assert client.sent == ["x"]
    assert vectors[0] == vectors[1]


def test_cache_survives_a_new_process(tmp_path):
    path = tmp_path / "cache.json"
    CachedEmbedder(CountingClient(), path).embed_documents(["a"])

    fresh_client = CountingClient()
    CachedEmbedder(fresh_client, path).embed_documents(["a"])

    assert fresh_client.sent == []


def test_another_model_does_not_reuse_vectors(tmp_path):
    path = tmp_path / "cache.json"
    CachedEmbedder(CountingClient(), path).embed_documents(["a"])

    other = CountingClient(SETTINGS.model_copy(update={"model": "gemini-embedding-2"}))
    CachedEmbedder(other, path).embed_documents(["a"])

    assert other.sent == ["a"]  # same text, different model: embedded again


def test_query_uses_the_instruction_text(tmp_path):
    client = CountingClient()

    CachedEmbedder(client, tmp_path / "c.json").embed_query("python", task="Find jobs")

    assert client.sent == ["Instruct: Find jobs\nQuery: python"]
