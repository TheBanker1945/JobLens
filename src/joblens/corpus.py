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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from joblens.extraction.schema import VacancyDetails
from joblens.extraction.store import DetailsStore
from joblens.sources.base import Vacancy, dedupe
from joblens.sources.store import VacancyStore

# src/joblens/corpus.py -> src/joblens -> src -> the repo root.
ROOT = Path(__file__).resolve().parents[2]

Name = Literal["samples", "raw"]
NAMES: tuple[Name, ...] = ("samples", "raw")


class Funnel(BaseModel):
    """What was dropped before anything could be ranked, and why.

    Every count here is a rejection with no score attached. A stored ranking
    explains why vacancy #83 was not read; it cannot say a word about a vacancy
    that was never in the ranking at all, because it was an open-application
    page, a copy of a job from another board, or had never been extracted and so
    was never embedded. Those are the quietest rejections in the system, so they
    are counted where they happen and carried on the corpus.

    A pydantic model rather than a dataclass because it is stored: a run keeps
    the funnel it was ranked out of (cv/runs.py).
    """

    loaded: int = 0  # what the store held
    not_a_vacancy: int = 0  # open applications; see NOT_A_VACANCY below
    duplicates: int = 0  # the same job found on two boards
    not_extracted: int = 0  # no fields, so it cannot be embedded like the rest

    @property
    def dropped(self) -> int:
        return self.not_a_vacancy + self.duplicates + self.not_extracted

    def line(self) -> str:
        """One line, and it says nothing when nothing was dropped."""
        if not self.loaded:
            return ""
        reasons = [
            (self.not_a_vacancy, "open applications"),
            (self.duplicates, "duplicates"),
            (self.not_extracted, "never extracted"),
        ]
        named = ", ".join(f"{count} {what}" for count, what in reasons if count)
        kept = self.loaded - self.dropped
        if not named:
            return f"{kept} of {self.loaded} stored vacancies could be ranked"
        return (
            f"{kept} of {self.loaded} stored vacancies could be ranked; "
            f"{self.dropped} never had a chance: {named}"
        )


@dataclass(frozen=True)
class Corpus:
    """Vacancies plus whatever has been extracted from them."""

    name: str
    vacancies: list[Vacancy]
    details: dict[str, VacancyDetails]  # by Vacancy.key; missing = not extracted yet
    funnel: Funnel = field(default_factory=Funnel)  # dropped on the way here

    def extracted(self) -> "Corpus":
        """Only the vacancies that have been extracted.

        Every document style but `raw` is built from extracted fields, so a
        vacancy without them cannot be embedded the same way as the rest. The
        eval compares variants over one fixed set of vacancies, and dropping a
        vacancy from some variants but not others would make the scores
        incomparable -- so it is dropped from all of them, here, once.
        """
        keep = [v for v in self.vacancies if v.key in self.details]
        funnel = self.funnel.model_copy(
            update={"not_extracted": len(self.vacancies) - len(keep)}
        )
        return Corpus(self.name, keep, self.details, funnel)

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
    return Corpus("samples", vacancies, details, Funnel(loaded=len(vacancies)))


# "Open sollicitatie", "Open application": a page inviting you to send a CV when
# nothing fits. It is not a job, so it can never be the right answer to a search
# -- and it is the worst kind of wrong answer, because its text ("tell us who you
# are and what you are looking for") is shaped like a *query* rather than like a
# vacancy, which puts it close to every query at once. Measured in milestone 3.1:
# two such pages in 202 cost the weakest variant 22 points of hit@1.
NOT_A_VACANCY = re.compile(
    r"^\s*open\s+(sollicitatie|application|applications)\b", re.I
)


def is_vacancy(vacancy: Vacancy) -> bool:
    """Whether this is an actual job rather than an invitation to write in."""
    return not NOT_A_VACANCY.match(vacancy.title)


def _load_raw(directory: Path) -> Corpus:
    store = VacancyStore(directory / "vacancies")
    sources = store.sources()
    records = DetailsStore(directory / "extracted").load_all(sources)
    vacancies = [vacancy for source in sources for vacancy in store.load(source)]
    # Filtered on the way out rather than on the way in: the store keeps what the
    # boards actually published, and what counts as searchable is a decision we
    # can change and re-measure without fetching anything again. Counted on the
    # way out too, for the same reason: this is where a vacancy disappears.
    jobs = [v for v in vacancies if is_vacancy(v)]
    kept = dedupe(jobs)
    funnel = Funnel(
        loaded=len(vacancies),
        not_a_vacancy=len(vacancies) - len(jobs),
        duplicates=len(jobs) - len(kept),
    )
    details = {key: record.details for key, record in records.items()}
    return Corpus("raw", kept, details, funnel)
