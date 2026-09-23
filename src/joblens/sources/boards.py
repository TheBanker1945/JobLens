"""Employer boards: which ones JobLens reads, and finding more of them.

Almost every Dutch vacancy starts on an employer's own board, and the job sites
copy it from there. Indeed says where it copied from (`job_url_direct`), and so
does jobdataapi (`url`). Measured 2026-09-23 on 551 stored vacancies: those
links point at 15 Recruitee boards, 32 SmartRecruiters postings, Greenhouse in
three spellings, and a long tail of platforms we cannot read yet.

So the aggregators are also a **discovery layer**. This module reads their
links and names the boards behind them; `scripts/discover_boards.py` checks each
one with a single request and lets a person accept it into boards.toml. Reading
a board directly is better than reading a copy of it: the whole text, every job
the employer lists, and no board to be blocked by.

boards.toml is committed. Company boards are public, and a fork should not have
to rediscover them.
"""

import re
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from joblens.sources.base import Vacancy

# The platforms with an adapter, and how their links name a board. Measured on
# stored links; Greenhouse writes the same board three ways.
READABLE: tuple[tuple[str, re.Pattern], ...] = (
    ("recruitee", re.compile(r"^https?://([a-z0-9-]+)\.recruitee\.com/", re.I)),
    (
        "greenhouse",
        re.compile(
            r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/"
            r"(?!embed/)([A-Za-z0-9_-]+)",
            re.I,
        ),
    ),
    ("smartrecruiters", re.compile(r"^https?://jobs\.smartrecruiters\.com/([^/?]+)/")),
)
# A short link (grnh.se/yhp3vzer2us): one request to see where it goes. On
# 2026-09-23 the one tried went to the employer's own careers page, which names
# no board; the check says so rather than guessing.
SHORT_LINK = re.compile(r"^https?://grnh\.se/([A-Za-z0-9]+)", re.I)
# Recruitee under an employer's own name uses /o/{job} as its job path. So does
# at least one other system (werkenbijheras.nl, 404 on /api/offers/), so an /o/
# link is only a candidate until /api/offers/ answers.
RECRUITEE_PATH = re.compile(r"/o/[^/]+")
# Platforms seen in stored links that have no adapter yet: counted, so the next
# adapter is chosen by what the links say rather than by guess.
NOT_READABLE: tuple[tuple[str, re.Pattern], ...] = (
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?]+)", re.I)),
    ("workday", re.compile(r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", re.I)),
    ("teamtailor", re.compile(r"([a-z0-9-]+)\.teamtailor\.com", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([^/?]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([^/?]+)", re.I)),
    ("jobylon", re.compile(r"(jobylon)\.com", re.I)),
    ("bamboohr", re.compile(r"([a-z0-9-]+)\.bamboohr\.com", re.I)),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.", re.I)),
    ("successfactors", re.compile(r"([a-z0-9.-]+)\.hr\.cloud\.sap", re.I)),
)
BOARD_KEYS = {"recruitee": "slug", "greenhouse": "slug", "smartrecruiters": "company"}


@dataclass(frozen=True)
class Candidate:
    """A board that stored links point at. `platform` is "grnh.se" for a short
    link that still has to be followed."""

    platform: str
    board: str
    links: int  # how many stored vacancies point here
    example: str


def outbound_links(vacancy: Vacancy) -> list[str]:
    """Where a copied vacancy says it came from: Indeed's `job_url_direct`,
    jobdataapi's `url` and `application_url`."""
    raw = vacancy.raw
    found = [raw.get("job_url_direct"), raw.get("url"), raw.get("application_url")]
    return [link for link in found if isinstance(link, str) and link.startswith("http")]


def candidates(
    vacancies: Iterable[Vacancy], known: Mapping[str, set[str]]
) -> tuple[list[Candidate], Counter]:
    """The readable boards the links point at that `known` does not have yet,
    most-linked first, and a count of links to platforms we cannot read."""
    seen: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], str] = {}
    unreadable: Counter[str] = Counter()
    for vacancy in vacancies:
        for link in outbound_links(vacancy):
            key = board_of(link)
            if key is None:
                kind = next((n for n, p in NOT_READABLE if p.search(link)), None)
                if kind:
                    unreadable[kind] += 1
                continue
            seen[key] += 1
            examples.setdefault(key, link)
    found = [
        Candidate(platform, board, links, examples[(platform, board)])
        for (platform, board), links in seen.most_common()
        if board.lower() not in {b.lower() for b in known.get(platform, set())}
    ]
    return found, unreadable


def board_of(link: str) -> tuple[str, str] | None:
    """("recruitee", "channable"), ("grnh.se", code), or None."""
    for platform, pattern in READABLE:
        if match := pattern.search(link):
            return platform, match.group(1)
    if match := SHORT_LINK.search(link):
        return "grnh.se", match.group(1)
    if RECRUITEE_PATH.search(urlparse(link).path):
        host = urlparse(link).netloc.lower()
        if not any(pattern.search(link) for _, pattern in NOT_READABLE):
            return "recruitee", host
    return None


def load_boards(path: Path) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def known_boards(boards: Mapping[str, list[dict]]) -> dict[str, set[str]]:
    """{"recruitee": {"channable", "vacatures.coloriet.nl"}, ...}"""
    return {
        platform: {entry[key] for entry in boards.get(platform, [])}
        for platform, key in BOARD_KEYS.items()
    }


def load_config(sources: Path, boards: Path) -> dict:
    """sources.toml with the board lists of boards.toml added: one dict, the
    way the fetch script has always read it."""
    config = tomllib.loads(sources.read_text(encoding="utf-8"))
    for platform, entries in load_boards(boards).items():
        config[platform] = [*config.get(platform, []), *entries]
    return config


def append_board(path: Path, platform: str, board: str, note: str) -> None:
    """Add one accepted board to boards.toml, with the numbers that got it in."""
    key = BOARD_KEYS[platform]
    entry = f'\n# {note}\n[[{platform}]]\n{key} = "{board}"\n'
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
