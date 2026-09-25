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
        """Add one entry, keeping whatever other processes added meanwhile.

        Two processes share this file more often than it looks: a match in one
        terminal while an eval runs in another, or a second worktree whose
        data/cache is a link to this one (both on 2026-09-24). Writing back the
        copy read at startup deleted every entry the other had added since, and
        emptying the file before refilling it let the other read half of it.
        So the file is read again just before writing, and replaced in one step.
        Two writes in the same few milliseconds can still lose one entry; this is
        a cache, and the price of that is one extraction bought twice.
        """
        self.entries = self._read() | self.entries
        self.entries[self._key(kind, model, text)] = payload
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
