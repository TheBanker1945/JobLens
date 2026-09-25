"""Cache for what a model made of a CV, so a rerun is free.

Reading a CV costs half a cent and three seconds, and the 3.5 eval reads the same
three CVs for every variant it compares. Keyed by the exact text the model saw,
the way `CachedEmbedder` keys a vector by the exact text it embedded: change a
word in the CV, or the model, and it is a different entry rather than a stale one.

This holds derived personal data -- a profile is someone's career in JSON -- so it
lives in `data/cache/`, which git ignores, and never next to the code.

It is also where every judgement an eval paid for is kept, and more than one
process writes it: `match_cv.py` in one terminal, an eval in another. So a write
reads the file again and adds to what is there, and swaps the result in whole
(6.1). Without the first, the process that writes last deletes whatever the other
one added since it started; without the second, a crash mid-write leaves half a
JSON file and every entry in it unreadable.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path


class CVCache:
    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = self._read()

    def get(self, kind: str, model: str, text: str) -> dict | None:
        return self.entries.get(self._key(kind, model, text))

    def put(self, kind: str, model: str, text: str, payload: dict) -> None:
        key = self._key(kind, model, text)
        self.entries = self._read() | self.entries
        self.entries[key] = payload
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
        )
        try:
            with handle:
                handle.write(json.dumps(self.entries))
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    def _read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _key(self, kind: str, model: str, text: str) -> str:
        return hashlib.sha256(f"{kind}\0{model}\0{text}".encode()).hexdigest()
