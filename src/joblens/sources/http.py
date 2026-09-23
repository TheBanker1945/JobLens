"""Shared HTTP helper for vacancy sources: polite by default.

`new_client` builds the client every source uses. The fetch script hands it a
`PoliteTransport` (sources/polite.py), so every request a source makes is paced,
counted and checked for a refusal without the source knowing about it.
"""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

USER_AGENT = "JobLens/0.1 (learning project; +https://github.com/TheBanker1945/JobLens)"


class RateLimited(Exception):
    """The source asked us to slow down. `retry_after` is in seconds."""

    def __init__(self, source: str, retry_after: float | None):
        wait = f" for {retry_after:.0f}s" if retry_after else ""
        super().__init__(f"{source} rate limited us{wait}")
        self.retry_after = retry_after


def new_client(
    timeout: float = 30.0, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """A client that identifies itself, so a site owner can see who we are.

    `transport` is where requests actually go: a `PoliteTransport` in a real
    fetch, a fake one in tests that answers locally.
    """
    return httpx.Client(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, transport=transport
    )


def get_json(client: httpx.Client, url: str, source: str, params: dict | None = None):
    response = client.get(url, params=params)
    if response.status_code == 429:
        raise RateLimited(source, retry_after_seconds(response.headers))
    response.raise_for_status()
    return response.json()


def retry_after_seconds(
    headers: httpx.Headers, now: datetime | None = None
) -> float | None:
    """How long a `Retry-After` header asks us to wait, in seconds, or None.

    The header comes in two forms: a number of seconds ("2710", what jobdataapi
    sends) or a date ("Wed, 23 Sep 2026 03:00:00 GMT"). Reading only the first
    with float() would crash the whole run on the second.
    """
    value = (headers.get("retry-after") or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    now = now or datetime.now(UTC)
    return max(0.0, (when - now).total_seconds())
