"""One shape for every vacancy, whatever API it came from.

Each source returns `Vacancy` objects, so everything downstream (extraction,
search, matching) never knows where a vacancy came from. Adding a source later
means one new adapter file plus a line in sources.toml.
"""

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class Vacancy(BaseModel):
    source: str  # "recruitee", "greenhouse", "jobdataapi"
    source_id: str  # id at that source; with `source` it is unique
    url: str
    title: str
    company: str | None = None
    city: str | None = None
    country: str | None = None
    posted_at: datetime | None = None  # when the employer published it
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    text: str  # plain text, contact details removed
    # as received, minus contact details: they are personal data (see clean.py)
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def fingerprint(self) -> str:
        """What makes this the same job as one found on another board.

        Boards copy each other: the same vacancy shows up on Indeed, on LinkedIn
        and on the company's own Greenhouse board, and the matcher should rank it
        once. Same title, same company, same place counts as the same job.

        Deliberately strict. Missing a duplicate costs one extra vacancy in the
        store; merging two jobs that merely look alike loses a real one. With no
        company name there is nothing to compare, so the vacancy keeps its own
        key and can never match another.
        """
        if not self.company:
            return self.key
        return "|".join(
            simplify(part) for part in (self.title, self.company, self.city or "")
        )

    @property
    def content_fingerprint(self) -> str:
        """The same job recognised by what it says, not by how it is labelled.

        `fingerprint` compares title, company and place, and the real corpus
        broke it twice: one job was posted by two names of the same business
        (BOSMAN and MediReva), and one had no company at all, which makes
        `fingerprint` give up and return the key. Both pairs carry byte-identical
        text.

        Identical text alone is not enough. Catawiki posted a Category Manager
        for Coins & Banknotes and one for E-Commerce with the same description
        pasted into both -- two real jobs, one description. So the title counts
        too, and `simplify` makes one title of "(Algemeen of Gespecialiseerd)
        verpleegkundige" and "Algemeen of Gespecialiseerd Verpleegkundige".

        Place is deliberately not in here: it is compared separately, because
        "UT" and "Utrecht" are one place and Amsterdam and Rotterdam are not.
        """
        body = " ".join(self.text.split()).encode()
        return f"{simplify(self.title)}|{hashlib.sha256(body).hexdigest()}"


class SeenJobs:
    """The jobs we already have, so the next one can be checked against them.

    Two ways of recognising a job we have seen: what it calls itself
    (`fingerprint`) and what it actually says (`content_fingerprint`). The
    second needs the place checked separately -- the same advert under two
    company names is one job, the same advert in two cities is two -- which a
    plain set of strings cannot express, so this holds the places per content.
    """

    def __init__(self) -> None:
        self._labels: set[str] = set()
        self._content: dict[str, list[str | None]] = {}

    def add(self, vacancy: Vacancy) -> None:
        self._labels.add(vacancy.fingerprint)
        self._content.setdefault(vacancy.content_fingerprint, []).append(vacancy.city)

    def has(self, vacancy: Vacancy) -> bool:
        if vacancy.fingerprint in self._labels:
            return True
        cities = self._content.get(vacancy.content_fingerprint)
        return cities is not None and any(
            same_place(city, vacancy.city) for city in cities
        )

    def __len__(self) -> int:
        return len(self._labels)


def same_place(one: str | None, other: str | None) -> bool:
    """Whether two place names can be the same place.

    Missing counts as compatible: a board that does not say where the job is
    cannot be used to argue it is somewhere else. An abbreviation counts too --
    one board wrote "UT" where another wrote "Utrecht" -- but only when it is
    short enough to be an abbreviation, so Amsterdam and Amersfoort stay apart.
    """
    if not one or not other:
        return True
    first, second = simplify(one), simplify(other)
    if first == second:
        return True
    short, long = sorted((first, second), key=len)
    return len(short) <= 3 and long.startswith(short)


def dedupe(vacancies: list[Vacancy]) -> list[Vacancy]:
    """The same list with later copies of a job dropped, the first one kept.

    Used when loading a store that was written before duplicates were caught
    this well; new fetches are filtered by VacancyStore.add instead.
    """
    seen, kept = SeenJobs(), []
    for vacancy in vacancies:
        if seen.has(vacancy):
            continue
        seen.add(vacancy)
        kept.append(vacancy)
    return kept


class VacancySource(Protocol):
    """A place vacancies come from: one company board, or a whole aggregator."""

    name: str

    def fetch(self, limit: int) -> list[Vacancy]: ...


def simplify(value: str) -> str:
    """Lower case, letters and digits only: "(Senior) Data Engineer" and
    "Senior data engineer" are one job written two ways."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
