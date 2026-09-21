"""Store vacancies as JSON Lines in data/raw/, which is never committed.

One file per source, one JSON object per line: easy to append, easy to read back
line by line, and a partly written file does not corrupt what came before.

Two kinds of duplicate are skipped. The same vacancy from the same source (same
id) is the obvious one. The same vacancy from a *different* source is the other:
one job is advertised on Indeed, on LinkedIn and on the company's own board at
once, and the matcher should not rank it three times. `Vacancy.fingerprint`
decides what counts as the same job.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from joblens.sources.base import SeenJobs, Vacancy


@dataclass(frozen=True)
class StoreResult:
    """What `add` did, so a run report can say why little was stored."""

    stored: int = 0
    known: int = 0  # same source, same id: seen in an earlier run
    duplicate: int = 0  # the same job, found through another source


class VacancyStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self._seen: SeenJobs | None = None

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

    def sources(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.jsonl"))

    def seen(self) -> SeenJobs:
        """Every job already stored, whichever source it came from.

        Read once and then kept up to date by `add`: a run adds a few dozen
        vacancies and would otherwise re-read the whole store for each check.
        """
        if self._seen is None:
            self._seen = SeenJobs()
            for source in self.sources():
                for vacancy in self.load(source):
                    self._seen.add(vacancy)
        return self._seen

    def add(self, vacancies: list[Vacancy]) -> StoreResult:
        """Append the ones we do not have yet, from any source."""
        if not vacancies:
            return StoreResult()
        source = vacancies[0].source
        known = self.existing_keys(source)
        seen = self.seen()
        fresh: list[Vacancy] = []
        known_again = duplicates = 0
        for vacancy in vacancies:
            if vacancy.key in known:
                known_again += 1
                continue
            if seen.has(vacancy):
                duplicates += 1
                continue
            known.add(vacancy.key)
            seen.add(vacancy)
            fresh.append(vacancy)

        if fresh:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.path_for(source).open("a", encoding="utf-8") as handle:
                for vacancy in fresh:
                    handle.write(vacancy.model_dump_json() + "\n")
        return StoreResult(len(fresh), known_again, duplicates)

    def load(self, source: str) -> list[Vacancy]:
        path = self.path_for(source)
        if not path.exists():
            return []
        return [
            Vacancy.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
