"""One company's board on Greenhouse: boards-api.greenhouse.io/v1/boards/{slug}/jobs

Public JSON, no key. `content` arrives HTML-escaped (&lt;p&gt;), so it has to be
unescaped once before the HTML can be turned into text. Greenhouse gives only a
free-text location ("Amsterdam", "Paris"), no country code.
"""

import html
from datetime import datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import redact, to_clean_text
from joblens.sources.http import get_json


class GreenhouseSource:
    name = "greenhouse"

    def __init__(self, slug: str, client: httpx.Client):
        self.slug = self.board = slug  # `board`: which listing closes its jobs
        self.client = client

    def fetch(self, limit: int | None = None) -> list[Vacancy]:
        """The whole board unless `limit` says otherwise.

        One request returns every job worldwide, so a limit here saves no
        request -- and applied before the Dutch filter it only loses Dutch
        jobs. The fetch script asks for the whole board and caps after the
        filter (sources/netherlands.py).
        """
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs"
        data = get_json(self.client, url, self.name, params={"content": "true"})
        return [self._vacancy(job) for job in data.get("jobs", [])[:limit]]

    def _vacancy(self, job: dict) -> Vacancy:
        content = html.unescape(job.get("content", ""))  # &lt;p&gt; -> <p>
        return Vacancy(
            source=self.name,
            source_id=str(job["id"]),
            url=job.get("absolute_url", ""),
            title=job["title"].strip(),
            company=job.get("company_name") or self.slug,
            city=(job.get("location") or {}).get("name"),
            country=None,  # Greenhouse does not provide one
            posted_at=_parse_date(job.get("first_published") or job.get("updated_at")),
            text=to_clean_text(content),
            raw=redact(job),
        )


def _parse_date(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
