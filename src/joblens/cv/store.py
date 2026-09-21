"""Cache for what a model made of a CV, so a rerun is free.

Reading a CV costs half a cent and three seconds, and the 3.5 eval reads the same
three CVs for every variant it compares. Keyed by the exact text the model saw,
the way `CachedEmbedder` keys a vector by the exact text it embedded: change a
word in the CV, or the model, and it is a different entry rather than a stale one.

This holds derived personal data -- a profile is someone's career in JSON -- so it
lives in `data/cache/`, which git ignores, and never next to the code.
"""

import hashlib
import json
from pathlib import Path


class CVCache:
    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = {}
        if path.exists():
            self.entries = json.loads(path.read_text(encoding="utf-8"))

    def get(self, kind: str, model: str, text: str) -> dict | None:
        return self.entries.get(self._key(kind, model, text))

    def put(self, kind: str, model: str, text: str, payload: dict) -> None:
        self.entries[self._key(kind, model, text)] = payload
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries), encoding="utf-8")

    def _key(self, kind: str, model: str, text: str) -> str:
        return hashlib.sha256(f"{kind}\0{model}\0{text}".encode()).hexdigest()
