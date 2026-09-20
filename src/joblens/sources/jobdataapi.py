"""jobdataapi.com: an aggregator, many employers behind one endpoint.

Different shape from the ATS boards: one URL covers every company, with filters
(country, age, title). The free tier is anonymous and limited per IP: about 10
requests an hour, 20 jobs per response, no paging. Measured 2026-09-20: rejected
requests count too, and a 429 carries `Retry-After` (we saw 2710 seconds).

So: we never retry, we stop at the first 429, and each request uses a different
filter so every response brings new vacancies. This source is a nice-to-have next
to the ATS boards, never something JobLens depends on.
"""

from datetime import datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import to_clean_text
from joblens.sources.http import RateLimited, get_json

BASE_URL = "https://jobdataapi.com/api/jobs/"
PER_REQUEST = 20  # anonymous cap: more is ignored, paging is not allowed

# Each filter is one request; varied on purpose, so the 20 results differ and the
# sample is not all tech jobs (the ATS boards already skew that way).
DEFAULT_FILTERS: tuple[dict, ...] = (
    {"title": "verpleegkundige"},
    {"title": "monteur"},
    {"title": "logistiek"},
    {"title": "docent"},
    {"title": "adviseur"},
    {"title": "developer"},
)


class JobDataApiSource:
    name = "jobdataapi"

    def __init__(
        self,
        client: httpx.Client,
        country: str = "NL",
        max_age_days: int = 7,
        filters: tuple[dict, ...] = DEFAULT_FILTERS,
    ):
        self.client = client
        self.country = country
        self.max_age_days = max_age_days
        self.filters = filters

    def fetch(self, limit: int = 100) -> list[Vacancy]:
        vacancies: list[Vacancy] = []
        for extra in self.filters:
            if len(vacancies) >= limit:
                break
            params = {
                "country_code": self.country,
                "max_age": self.max_age_days,
                **extra,
            }
            try:
                data = get_json(self.client, BASE_URL, self.name, params=params)
            except RateLimited:
                raise  # the caller decides whether to wait; we never retry
            for job in data.get("results", [])[:PER_REQUEST]:
                vacancies.append(self._vacancy(job))
        return vacancies[:limit]

    def _vacancy(self, job: dict) -> Vacancy:
        locations = job.get("locations") or []
        city = next((loc.get("city") for loc in locations if loc.get("city")), None)
        return Vacancy(
            source=self.name,
            source_id=str(job["id"]),
            url=job.get("application_url") or job.get("url") or "",
            title=(job.get("title") or "").strip(),
            company=(job.get("company") or {}).get("name"),
            city=city,
            country=self.country,
            posted_at=_parse_date(job.get("published")),
            text=to_clean_text(job.get("description", "")),
            raw=job,
        )


def _parse_date(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
