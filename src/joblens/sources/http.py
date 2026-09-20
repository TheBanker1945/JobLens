"""Shared HTTP helper for vacancy sources: polite by default."""

import httpx

USER_AGENT = "JobLens/0.1 (learning project; +https://github.com/TheBanker1945/JobLens)"


class RateLimited(Exception):
    """The source asked us to slow down. `retry_after` is in seconds."""

    def __init__(self, source: str, retry_after: float | None):
        wait = f" for {retry_after:.0f}s" if retry_after else ""
        super().__init__(f"{source} rate limited us{wait}")
        self.retry_after = retry_after


def new_client(timeout: float = 30.0) -> httpx.Client:
    """A client that identifies itself, so a site owner can see who we are."""
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})


def get_json(client: httpx.Client, url: str, source: str, params: dict | None = None):
    response = client.get(url, params=params)
    if response.status_code == 429:
        retry_after = response.headers.get("retry-after")
        raise RateLimited(source, float(retry_after) if retry_after else None)
    response.raise_for_status()
    return response.json()
