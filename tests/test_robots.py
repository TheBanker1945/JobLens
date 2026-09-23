"""robots.txt: checked for what is crawled, and only for that.

A fake site answers robots.txt and pages; a fake clock means nothing waits.
"""

import httpx
import pytest

from joblens.sources.http import new_client
from joblens.sources.polite import (
    CRAWL,
    Disallowed,
    FetchState,
    Gate,
    PoliteTransport,
    Refused,
    Rules,
)


class FakeTime:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 6))
        self.t += seconds


def site(robots: str | None, robots_status: int = 200, robots_type="text/plain"):
    """A site whose robots.txt says `robots`, and whose pages all answer 200."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.query.decode()
        seen.append(request.url.path + (f"?{query}" if query else ""))
        if request.url.path == "/robots.txt":
            return httpx.Response(
                robots_status, text=robots or "", headers={"content-type": robots_type}
            )
        return httpx.Response(200, text="<html>a vacancy</html>")

    fake = FakeTime()
    gate = Gate(
        FetchState(), Rules(jitter_seconds=0), sleep=fake.sleep, clock=fake.clock
    )
    client = new_client(transport=PoliteTransport(gate, httpx.MockTransport(handler)))
    return client, seen, gate, fake


def test_a_crawl_reads_robots_txt_once_per_run():
    client, seen, _, _ = site("User-agent: *\nDisallow: /login\n")

    client.get("https://www.example.nl/vacatures/a", extensions=CRAWL)
    client.get("https://www.example.nl/vacatures/b", extensions=CRAWL)

    assert seen == ["/robots.txt", "/vacatures/a", "/vacatures/b"]


def test_a_disallowed_page_is_never_requested():
    client, seen, _, _ = site("User-agent: *\nDisallow: /jobs-guest/\n")

    with pytest.raises(Disallowed) as info:
        client.get("https://www.example.nl/jobs-guest/1", extensions=CRAWL)

    assert info.value.status == "disallowed"
    assert seen == ["/robots.txt"]


def test_wildcards_are_read_the_way_rfc_9309_says():
    """nationalevacaturebank.nl, 2026-09-23: `Disallow: /vacatures/*?page=`.
    The standard library's robotparser reads the `*` literally and allows it."""
    client, seen, _, _ = site("User-agent: *\nDisallow: /vacatures/*?page=\n")

    with pytest.raises(Disallowed):
        client.get("https://www.example.nl/vacatures/ict?page=2", extensions=CRAWL)
    client.get("https://www.example.nl/vacatures/ict", extensions=CRAWL)

    assert seen == ["/robots.txt", "/vacatures/ict"]


def test_an_api_request_is_not_a_crawl():
    """jobdataapi.com disallows /api/ in robots.txt and documents /api/ as its
    product. A documented API is used as documented: robots.txt is not read."""
    client, seen, _, _ = site("User-agent: *\nDisallow: /\n")

    client.get("https://jobdataapi.com/api/jobs/")

    assert seen == ["/api/jobs/"]


def test_no_robots_txt_allows_everything():
    client, seen, _, _ = site(None, robots_status=404)

    client.get("https://www.example.nl/vacatures/a", extensions=CRAWL)

    assert seen == ["/robots.txt", "/vacatures/a"]


def test_a_robots_txt_that_cannot_be_read_allows_nothing():
    """RFC 9309: a server error on robots.txt means assume everything is
    disallowed, until it can be read again (the next run)."""
    client, seen, _, _ = site(None, robots_status=503)

    with pytest.raises(Disallowed, match="could not be read"):
        client.get("https://www.example.nl/vacatures/a", extensions=CRAWL)

    assert seen == ["/robots.txt"]


def test_a_crawl_delay_slows_the_gate_down():
    """academictransfer.com asks for Crawl-delay: 10; our own is 1.5 s."""
    client, _, _, fake = site("User-agent: *\nCrawl-delay: 10\n")

    client.get("https://www.example.nl/vacatures/a", extensions=CRAWL)
    client.get("https://www.example.nl/vacatures/b", extensions=CRAWL)

    assert fake.slept[-1] == 10


def test_a_request_rate_faster_than_ours_changes_nothing():
    """werkenbijdeoverheid.nl: Request-rate 10/1 is 0.1 s; the gate keeps 1.5 s."""
    client, _, _, fake = site("User-agent: *\nRequest-rate: 10/1\n")

    client.get("https://www.example.nl/vacatures/a", extensions=CRAWL)
    client.get("https://www.example.nl/vacatures/b", extensions=CRAWL)

    assert fake.slept[-1] == 1.5


def test_a_challenge_page_on_robots_txt_is_a_refusal():
    """werkzoeken.nl answered robots.txt itself with Cloudflare's challenge."""
    challenge = "<html><script>window._cf_chl_opt={}</script></html>"
    client, seen, gate, _ = site(challenge, robots_status=403, robots_type="text/html")

    with pytest.raises(Refused, match="Cloudflare"):
        client.get("https://www.example.nl/vacatures/a", extensions=CRAWL)

    assert seen == ["/robots.txt"]
    assert "example.nl" in gate.state.sites
