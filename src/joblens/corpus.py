"""Which vacancies are we working with: the committed samples, or the real ones?

Two corpora, one shape. Everything downstream -- search, the retrieval eval, CV
matching later -- asks for a `Corpus` and never learns whether the vacancies came
from ten text files in the repo or from a scrape sitting in data/raw/.

- samples: the 10 fictional vacancies in data/samples/. Committed, so anyone who
  clones the repo measures the same thing. This is the public, reproducible test.
- raw:     the real vacancies in data/raw/, fetched by scripts/fetch_vacancies.py.
  Never committed, so numbers over this corpus are yours alone.

A vacancy is identified by `Vacancy.key` ("greenhouse:4536789"), in both corpora:
a sample becomes a Vacancy with source "sample" and the file stem as its id. That
is what labelled queries refer to, so one query file format fits both.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from joblens.extraction.schema import VacancyDetails
from joblens.extraction.store import DetailsStore
from joblens.sources.base import Vacancy
from joblens.sources.store import VacancyStore

# src/joblens/corpus.py -> src/joblens -> src -> the repo root.
ROOT = Path(__file__).resolve().parents[2]

Name = Literal["samples", "raw"]
NAMES: tuple[Name, ...] = ("samples", "raw")


@dataclass(frozen=True)
class Corpus:
    """Vacancies plus whatever has been extracted from them."""

    name: str
    vacancies: list[Vacancy]
    details: dict[str, VacancyDetails]  # by Vacancy.key; missing = not extracted yet

    def extracted(self) -> "Corpus":
        """Only the vacancies that have been extracted.

        Every document style but `raw` is built from extracted fields, so a
        vacancy without them cannot be embedded the same way as the rest. The
        eval compares variants over one fixed set of vacancies, and dropping a
        vacancy from some variants but not others would make the scores
        incomparable -- so it is dropped from all of them, here, once.
        """
        keep = [v for v in self.vacancies if v.key in self.details]
        return Corpus(self.name, keep, self.details)

    def by_key(self) -> dict[str, Vacancy]:
        return {vacancy.key: vacancy for vacancy in self.vacancies}

    def __len__(self) -> int:
        return len(self.vacancies)


def load_corpus(name: Name, root: Path = ROOT) -> Corpus:
    if name == "samples":
        return _load_samples(root / "data" / "samples", root)
    if name == "raw":
        return _load_raw(root / "data" / "raw")
    raise ValueError(f"unknown corpus {name!r}, expected one of {NAMES}")


def _load_samples(directory: Path, root: Path) -> Corpus:
    """The committed sample texts, as vacancies, with their stored extractions."""
    vacancies, details = [], {}
    for path in sorted((directory / "vacancies").glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        extracted = directory / "extracted" / f"{path.stem}.json"
        found = None
        if extracted.exists():
            payload = json.loads(extracted.read_text(encoding="utf-8"))
            found = VacancyDetails.model_validate(payload["details"])
        # A sample is a text file, so the company and city it mentions are only
        # known once the text has been extracted.
        vacancy = Vacancy(
            source="sample",
            source_id=path.stem,
            url=str(path.relative_to(root)),  # a path, not a link: never absolute
            title=found.title if found else text.splitlines()[0],
            company=found.company if found else None,
            city=found.city if found else None,
            text=text,
        )
        vacancies.append(vacancy)
        if found:
            details[vacancy.key] = found
    return Corpus("samples", vacancies, details)


def _load_raw(directory: Path) -> Corpus:
    store = VacancyStore(directory / "vacancies")
    sources = store.sources()
    records = DetailsStore(directory / "extracted").load_all(sources)
    vacancies = [vacancy for source in sources for vacancy in store.load(source)]
    details = {key: record.details for key, record in records.items()}
    return Corpus("raw", vacancies, details)
