"""One company's career page on Recruitee: {slug}.recruitee.com/api/offers/

Public JSON, no key, no registration. The vacancy text is split over two fields,
`description` and `requirements`; the requirements hold the skills, so both are
needed.
"""

from datetime import datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import redact, to_clean_text
from joblens.sources.http import get_json


class RecruiteeSource:
    name = "recruitee"

    def __init__(self, slug: str, client: httpx.Client):
        self.slug = slug
        self.client = client

    def fetch(self, limit: int = 100) -> list[Vacancy]:
        url = f"https://{self.slug}.recruitee.com/api/offers/"
        offers = get_json(self.client, url, self.name).get("offers", [])
        return [self._vacancy(offer) for offer in offers[:limit]]

    def _vacancy(self, offer: dict) -> Vacancy:
        body = f"{offer.get('description', '')}\n{offer.get('requirements', '')}"
        return Vacancy(
            source=self.name,
            source_id=str(offer["id"]),
            url=offer.get("careers_url") or f"https://{self.slug}.recruitee.com",
            title=offer["title"].strip(),
            company=offer.get("company_name") or self.slug,
            city=offer.get("city"),
            country=offer.get("country_code"),
            posted_at=_parse_date(offer.get("published_at") or offer.get("created_at")),
            text=to_clean_text(body),
            raw=redact(offer),
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:  # Recruitee sends "2026-09-18 12:55:59 UTC"
        return datetime.fromisoformat(value.replace(" UTC", "+00:00"))
    except ValueError:
        return None
