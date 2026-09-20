"""Extracted details, kept next to the vacancies they came from.

Extraction costs a second and a fraction of a cent per vacancy, and a stored
vacancy text never changes, so the result is worth keeping: the second run over
119 vacancies should cost nothing.

One file per source, one JSON object per line, append-only. Extracting a vacancy
again -- with a better prompt, or another model -- appends a new line, and the
last line for a key wins. Nothing is ever rewritten in place, so a run that is
interrupted halfway cannot damage what was already there.
"""

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from joblens.extraction.schema import VacancyDetails


class ExtractedVacancy(BaseModel):
    """One extraction: whose text, which model, and what came out."""

    key: str  # "indeed:3fa1", the Vacancy it belongs to
    model: str
    details: VacancyDetails
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DetailsStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, source: str) -> Path:
        return self.directory / f"{source}.jsonl"

    def load(self, source: str) -> dict[str, ExtractedVacancy]:
        """Newest extraction per vacancy, by key."""
        path = self.path_for(source)
        if not path.exists():
            return {}
        newest: dict[str, ExtractedVacancy] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = ExtractedVacancy.model_validate_json(line)
                newest[record.key] = record  # a later line replaces an earlier one
        return newest

    def load_all(self, sources: list[str]) -> dict[str, ExtractedVacancy]:
        details: dict[str, ExtractedVacancy] = {}
        for source in sources:
            details |= self.load(source)
        return details

    def add(self, source: str, records: list[ExtractedVacancy]) -> int:
        if not records:
            return 0
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path_for(source).open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(record.model_dump_json() + "\n")
        return len(records)
