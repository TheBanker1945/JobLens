"""Store vacancies as JSON Lines in data/raw/, which is never committed.

One file per source, one JSON object per line: easy to append, easy to read back
line by line, and a partly written file does not corrupt what came before.
Vacancies already stored (same source and id) are skipped, so fetching twice
does not create duplicates.
"""

import json
from pathlib import Path

from joblens.sources.base import Vacancy


class VacancyStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, source: str) -> Path:
        return self.directory / f"{source}.jsonl"

    def existing_keys(self, source: str) -> set[str]:
        path = self.path_for(source)
        if not path.exists():
            return set()
        keys = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                keys.add(f"{record['source']}:{record['source_id']}")
        return keys

    def add(self, vacancies: list[Vacancy]) -> tuple[int, int]:
        """Append the new ones. Returns (stored, skipped)."""
        if not vacancies:
            return 0, 0
        source = vacancies[0].source
        known = self.existing_keys(source)
        fresh, skipped = [], 0
        for vacancy in vacancies:
            if vacancy.key in known:
                skipped += 1
                continue
            known.add(vacancy.key)
            fresh.append(vacancy)

        if fresh:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.path_for(source).open("a", encoding="utf-8") as handle:
                for vacancy in fresh:
                    handle.write(vacancy.model_dump_json() + "\n")
        return len(fresh), skipped

    def load(self, source: str) -> list[Vacancy]:
        path = self.path_for(source)
        if not path.exists():
            return []
        return [
            Vacancy.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
