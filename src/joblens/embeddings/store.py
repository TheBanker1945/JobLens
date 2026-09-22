"""Disk cache for embeddings, so repeated runs cost no time or money.

Embedding the same text always gives the same vector, so it only has to be
computed once. The key includes the model: different models produce different
vectors for the same text and must never share a cache entry.

**Why SQLite, and not the JSON file this used to be.** The JSON version held
every vector as text in one file and read the whole of it to answer anything:
for gemini-embedding-2 that was 200 MB and 3,008 vectors, of which one CV run
needs about 400. Measured 2026-09-22: 3.0 s and 545 MB of memory to open it on
every `match_cv.py` run, and 4.3 s to write all of it back whenever a single new
text was embedded -- once per query part, so a CV asked as six parts rewrote it
six times. A table with one row per vector reads only the rows a run asks for
and appends a new one without touching the rest. It is in the standard library,
it is one file, and a write either lands whole or not at all, which the JSON
rewrite could not promise.

**Why float32.** Every provider in the eval sends vectors that are exactly
representable in 32 bits: all three caches round-tripped through float32 with a
difference of zero. Storing them as 64-bit numbers would double the file and
change no ranking, and storing them as text was the 200 MB.

A cache written by the old version sits next to the new one as a `.json` file
and is imported once, the first time the new one is opened empty.
"""

import hashlib
import json
import sqlite3
from array import array
from pathlib import Path

from joblens.embeddings.client import DEFAULT_TASK, EmbeddingClient, Vector

# SQLite refuses a statement with more than 999 parameters on older builds; a
# batch of 500 keys stays well inside that everywhere.
KEYS_PER_QUERY = 500


def cache_path(cache_dir: Path, model: str) -> Path:
    """Where one model's vectors live. One file per model: one vector space."""
    return cache_dir / f"embeddings-{model.replace(':', '-')}.sqlite"


class CachedEmbedder:
    """Wraps an EmbeddingClient; only texts that are not cached hit the API."""

    def __init__(self, client: EmbeddingClient, path: Path):
        self.client = client
        self.path = path
        self.hits = 0
        self.misses = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB)"
        )
        self._import_legacy_json(path.with_suffix(".json"))

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        keys = [self._key(text) for text in texts]
        found = self._load(keys)
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in found]
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        if missing:
            vectors = self.client.embed_documents(missing)
            fresh = {
                self._key(text): vector
                for text, vector in zip(missing, vectors, strict=True)
            }
            self._store(fresh)
            found |= {key: _unpack(_pack(vector)) for key, vector in fresh.items()}
        return [found[key] for key in keys]

    def embed_query(self, query: str, task: str = DEFAULT_TASK) -> Vector:
        # Embed the instruction-wrapped text, so queries and documents share a cache.
        return self.embed_documents([self.client.query_text(query, task)])[0]

    def is_cached(self, text: str) -> bool:
        """Whether this text already has a vector, without asking the server."""
        row = self._db.execute(
            "SELECT 1 FROM vectors WHERE key = ?", (self._key(text),)
        ).fetchone()
        return row is not None

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def close(self) -> None:
        self._db.close()

    def _load(self, keys: list[str]) -> dict[str, Vector]:
        unique = list(dict.fromkeys(keys))
        found: dict[str, Vector] = {}
        for start in range(0, len(unique), KEYS_PER_QUERY):
            batch = unique[start : start + KEYS_PER_QUERY]
            marks = ",".join("?" * len(batch))
            rows = self._db.execute(
                f"SELECT key, vector FROM vectors WHERE key IN ({marks})", batch
            )
            found |= {key: _unpack(blob) for key, blob in rows}
        return found

    def _store(self, vectors: dict[str, Vector]) -> None:
        with self._db:  # one transaction: all of these land, or none of them
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors (key, vector) VALUES (?, ?)",
                [(key, _pack(vector)) for key, vector in vectors.items()],
            )

    def _import_legacy_json(self, legacy: Path) -> None:
        """Carry a cache from before 2026-09-22 over, once. The file is left be."""
        if not legacy.exists() or len(self):
            return
        vectors = json.loads(legacy.read_text(encoding="utf-8"))
        self._store(vectors)

    def _key(self, text: str) -> str:
        fingerprint = f"{self.client.settings.model}\0{text}".encode()
        return hashlib.sha256(fingerprint).hexdigest()


def _pack(vector: Vector) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> Vector:
    numbers = array("f")
    numbers.frombytes(blob)
    return numbers.tolist()
