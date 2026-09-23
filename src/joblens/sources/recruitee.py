"""One company's career page on Recruitee: {slug}.recruitee.com/api/offers/

Public JSON, no key, no registration. The vacancy text is split over two fields,
`description` and `requirements`; the requirements hold the skills, so both are
needed.

Many Dutch employers run Recruitee under their own name: vacatures.coloriet.nl
answers /api/offers/ exactly like coloriet.recruitee.com would (44 offers,
measured 2026-09-22). So a board is either a slug or a host name; a slug never
has a dot in it.
"""

from datetime import datetime

import httpx

from joblens.sources.base import Vacancy
from joblens.sources.clean import redact, to_clean_text
from joblens.sources.http import get_json


class RecruiteeSource:
    name = "recruitee"

    def __init__(self, slug: str, client: httpx.Client):
        """`slug` is "channable" for channable.recruitee.com, or a whole host
        name such as "vacatures.coloriet.nl" for a board on its own domain."""
        self.slug = self.board = slug  # `board`: which listing closes its jobs
        self.client = client
        self.base = (
            f"https://{slug}" if "." in slug else f"https://{slug}.recruitee.com"
        )

    def fetch(self, limit: int | None = None) -> list[Vacancy]:
        """The whole board unless `limit` says otherwise: one request returns
        every offer, so the script caps after the Dutch filter instead."""
        offers = get_json(self.client, f"{self.base}/api/offers/", self.name)
        return [self._vacancy(offer) for offer in offers.get("offers", [])[:limit]]

    def _vacancy(self, offer: dict) -> Vacancy:
        body = f"{offer.get('description', '')}\n{offer.get('requirements', '')}"
        return Vacancy(
            source=self.name,
            source_id=str(offer["id"]),
            url=offer.get("careers_url") or self.base,
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
