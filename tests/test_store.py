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
    cache = CachedEmbedder(client, tmp_path / "cache.sqlite")

    first = cache.embed_documents(["a", "bb"])
    second = cache.embed_documents(["a", "bb"])

    assert first == second
    assert client.sent == ["a", "bb"]  # sent once
    assert (cache.hits, cache.misses) == (2, 2)


def test_only_missing_texts_are_sent(tmp_path):
    client = CountingClient()
    cache = CachedEmbedder(client, tmp_path / "cache.sqlite")

    cache.embed_documents(["a"])
    cache.embed_documents(["a", "new"])

    assert client.sent == ["a", "new"]


def test_duplicates_are_embedded_once(tmp_path):
    client = CountingClient()

    vectors = CachedEmbedder(client, tmp_path / "c.sqlite").embed_documents(["x", "x"])

    assert client.sent == ["x"]
    assert vectors[0] == vectors[1]


def test_cache_survives_a_new_process(tmp_path):
    path = tmp_path / "cache.sqlite"
    CachedEmbedder(CountingClient(), path).embed_documents(["a"])

    fresh_client = CountingClient()
    CachedEmbedder(fresh_client, path).embed_documents(["a"])

    assert fresh_client.sent == []


def test_another_model_does_not_reuse_vectors(tmp_path):
    path = tmp_path / "cache.sqlite"
    CachedEmbedder(CountingClient(), path).embed_documents(["a"])

    other = CountingClient(SETTINGS.model_copy(update={"model": "gemini-embedding-2"}))
    CachedEmbedder(other, path).embed_documents(["a"])

    assert other.sent == ["a"]  # same text, different model: embedded again


def test_query_uses_the_instruction_text(tmp_path):
    client = CountingClient()

    CachedEmbedder(client, tmp_path / "c.sqlite").embed_query(
        "python", task="Find jobs"
    )

    assert client.sent == ["Instruct: Find jobs\nQuery: python"]


def test_what_is_cached_can_be_checked_without_the_server(tmp_path):
    """data_status.py asks this to report how much is already embedded."""
    cache = CachedEmbedder(CountingClient(), tmp_path / "cache.sqlite")
    cache.embed_documents(["een vacaturetekst"])

    assert cache.is_cached("een vacaturetekst")
    assert not cache.is_cached("een andere tekst")


def test_a_json_cache_from_before_is_imported_once_and_left_alone(tmp_path):
    """The old format was one JSON object of key -> vector next to the new file."""
    import json

    old = CachedEmbedder(CountingClient(), tmp_path / "scratch.sqlite")
    key = old._key("een vacature")
    legacy = tmp_path / "embeddings-m.json"
    legacy.write_text(json.dumps({key: [0.5, 0.25]}), encoding="utf-8")

    client = CountingClient()
    cache = CachedEmbedder(client, tmp_path / "embeddings-m.sqlite")

    assert cache.embed_documents(["een vacature"]) == [[0.5, 0.25]]
    assert client.sent == []  # came from the old file, not from the server
    assert legacy.exists()


def test_a_float32_vector_comes_back_exactly(tmp_path):
    """Providers send float32 values; storing them as float32 loses nothing."""

    class Precise(CountingClient):
        def embed_documents(self, texts):
            self.sent += texts
            return [[0.1234567, -0.000123, 3.0e-8] for _ in texts]

    path = tmp_path / "cache.sqlite"
    first = CachedEmbedder(Precise(), path).embed_documents(["a"])
    again = CachedEmbedder(Precise(), path).embed_documents(["a"])

    assert first == again  # the same numbers on the first run and every later one
    assert all(
        abs(x - y) < 1e-7
        for x, y in zip(first[0], [0.1234567, -0.000123, 3.0e-8], strict=True)
    )


def test_many_texts_are_stored_in_one_go(tmp_path):
    """More keys than one SQL statement may carry, looked up in batches."""
    client = CountingClient()
    cache = CachedEmbedder(client, tmp_path / "cache.sqlite")
    texts = [f"vacature {n}" for n in range(1200)]

    cache.embed_documents(texts)
    again = CachedEmbedder(CountingClient(), tmp_path / "cache.sqlite")

    assert len(again) == 1200
    assert again.embed_documents(texts) == cache.embed_documents(texts)
