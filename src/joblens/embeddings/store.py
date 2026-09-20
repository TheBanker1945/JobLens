"""Disk cache for embeddings, so repeated runs cost no time or money.

Embedding the same text always gives the same vector, so it only has to be
computed once. The key includes the model: different models produce different
vectors for the same text and must never share a cache entry.
"""

import hashlib
import json
from pathlib import Path

from joblens.embeddings.client import DEFAULT_TASK, EmbeddingClient, Vector


class CachedEmbedder:
    """Wraps an EmbeddingClient; only texts that are not cached hit the API."""

    def __init__(self, client: EmbeddingClient, path: Path):
        self.client = client
        self.path = path
        self.hits = 0
        self.misses = 0
        self._vectors: dict[str, Vector] = {}
        if path.exists():
            self._vectors = json.loads(path.read_text(encoding="utf-8"))

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in self._vectors]
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        if missing:
            for text, vector in zip(
                missing, self.client.embed_documents(missing), strict=True
            ):
                self._vectors[self._key(text)] = vector
            self.save()
        return [self._vectors[self._key(t)] for t in texts]

    def embed_query(self, query: str, task: str = DEFAULT_TASK) -> Vector:
        # Embed the instruction-wrapped text, so queries and documents share a cache.
        return self.embed_documents([self.client.query_text(query, task)])[0]

    def is_cached(self, text: str) -> bool:
        """Whether this text already has a vector, without asking the server."""
        return self._key(text) in self._vectors

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._vectors), encoding="utf-8")

    def _key(self, text: str) -> str:
        fingerprint = f"{self.client.settings.model}\0{text}".encode()
        return hashlib.sha256(fingerprint).hexdigest()
