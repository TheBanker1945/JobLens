"""One shape for every vacancy, whatever API it came from.

Each source returns `Vacancy` objects, so everything downstream (extraction,
search, matching) never knows where a vacancy came from. Adding a source later
means one new adapter file plus a line in sources.toml.
"""

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


class VacancySource(Protocol):
    """A place vacancies come from: one company board, or a whole aggregator."""

    name: str

    def fetch(self, limit: int) -> list[Vacancy]: ...


def simplify(value: str) -> str:
    """Lower case, letters and digits only: "(Senior) Data Engineer" and
    "Senior data engineer" are one job written two ways."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
