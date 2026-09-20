"""One shape for every vacancy, whatever API it came from.

Each source returns `Vacancy` objects, so everything downstream (extraction,
search, matching) never knows where a vacancy came from. Adding a source later
means one new adapter file plus a line in sources.toml.
"""

from datetime import datetime
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
    posted_at: datetime | None = None
    text: str  # plain text, contact details removed
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)  # as received

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


class VacancySource(Protocol):
    """A place vacancies come from: one company board, or a whole aggregator."""

    name: str

    def fetch(self, limit: int) -> list[Vacancy]: ...
