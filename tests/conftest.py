"""Shared test helpers.

`FakeClient` stands in for a model: it answers with scripted replies and records
what it was asked, so extraction can be tested without a server, a key or a
second of latency.

`build_pdf` is imported from scripts/make_sample_pdf.py rather than copied. That
script is the only PDF writer in the repository and it produces a file that is
committed (`data/samples/cvs/lisa_de_vries.pdf`), so the tests reading its output
are also what keeps it working.

`VacancyDetails` has no defaults on purpose -- the model must answer every
field, so "not in the text" is always a deliberate null rather than a field the
model forgot. That makes one useful in a test verbose to write, so it is built
here once.
"""

import os
import sys
from pathlib import Path

import psycopg
import pytest

from joblens.extraction.schema import VacancyDetails
from joblens.llm.types import ChatResult, Usage
from joblens.storage import Database

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from make_sample_pdf import build_pdf  # noqa: E402  (needs the path above)

SAMPLE_CVS = ROOT / "data" / "samples" / "cvs"


class FakeClient:
    """Satisfies the ChatClient interface; returns scripted replies in order.

    A reply may be a string, or a (content, finish_reason) pair when a test needs
    to see what happens to an answer the provider cut off.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, *, temperature=0.0, response_format=None, max_tokens=None):
        self.calls.append(
            {
                "messages": list(messages),
                "format": response_format,
                "max_tokens": max_tokens,
            }
        )
        content, finish_reason = self.replies.pop(0), "stop"
        if isinstance(content, tuple):
            content, finish_reason = content
        return ChatResult(
            finish_reason=finish_reason,
            content=content,
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            model="fake",
            latency_s=0.5,
        )

    def close(self):
        """The service closes every client it opens; a fake has nothing to close."""


# A CV damaged the way a real one was in 3.6.1: the PDF's own font maps some
# characters into the private use area, so the file does not contain what it
# displays. The glyphs here are the ones that CV really produced --
# \ue071-\ue077 were the digits 0-6, \ue081 and \ue082 brackets, \ue089 a
# dash, \ue09d the "+" of a phone number -- on invented content. The real CV is
# not in this repository and never will be, and it does not need to be: the
# damage is what has to be reproduced, not the person.
DAMAGED_CV = (
    "Jan Bakker\n"
    "Backend developer · AI & data\n"
    "Utrecht, NL · \ue09d31 6 18295250 · jan.bakker@example.com\n"
    "\n"
    "PROFIEL\n"
    "Backend developer \ue081MBO 4 Software Developer, 2026) met ervaring in "
    "Python,\n"
    "Node.js en PostgreSQL. Beschikbaar per direct, 32\ue08940 uur per week.\n"
    "\n"
    "WERKERVARING\n"
    "Backend Developer Dec \ue073\ue071\ue073\ue075 \ue089 Sep "
    "\ue073\ue071\ue073\ue077\n"
    "Van Dijk Software · Utrecht\n"
    "Bouwde REST APIs in Node.js en beheerde de database van het klantportaal.\n"
    "\n"
    "Stagiair Backend Feb \ue073\ue071\ue073\ue076 \ue089 Jun "
    "\ue073\ue071\ue073\ue076\n"
    "Blauwdruk · Amersfoort\n"
    "Werkte aan een dashboard met Python en schreef tests voor de API.\n"
    "\n"
    "OPLEIDING\n"
    "MBO Niveau 4 Software Developer, diploma behaald in 2026\n"
    "ROC Midden Nederland, Utrecht\n"
    "\n"
    "VAARDIGHEDEN\n"
    "Databases Supabase \ue081PostgreSQL\ue082, MongoDB, SQLite\n"
    "Talen Nederlands, Engels\n"
)


def scanned_pdf() -> bytes:
    """A PDF with a page and no text layer: what a scan looks like to a reader."""
    return build_pdf([[""]])


NOT_STATED = dict.fromkeys(
    (
        "company",
        "city",
        "work_mode",
        "hours_min",
        "hours_max",
        "salary_min",
        "salary_max",
        "salary_period",
        "salary_note",
        "education_level",
        "experience_years_min",
        "contract_type",
    )
)


def details(title: str, **stated) -> VacancyDetails:
    """Details with `title` and whatever else is given; the rest is null."""
    return VacancyDetails(
        title=title, skills=[], languages_required=[], **NOT_STATED | stated
    )


# The Postgres the database tests run against: the second database in the
# Docker container (compose.yaml, docker/initdb/). Without it they skip, so a
# clone without Docker still runs every other test.
TEST_DATABASE_URL = os.environ.get(
    "JOBLENS_TEST_DATABASE_URL",
    "postgresql://joblens:joblens@127.0.0.1:54320/joblens_test",
)
# One test run at a time on that database: several sessions share this repo,
# and one run's TRUNCATE in the middle of another's test is a failure nobody
# can reproduce. The second run waits for the first.
TEST_LOCK = 70_200_002


@pytest.fixture(scope="session")
def _test_database():
    try:
        conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as err:
        pytest.skip(
            f"no test database ({err.__class__.__name__}): start it with "
            "`docker compose up -d db`"
        )
    with conn:
        # Every test empties the tables. A URL that points anywhere but a
        # database named *_test is refused, so a mistyped variable can never
        # empty the one with your data in it.
        if not conn.info.dbname.endswith("_test"):
            pytest.fail(f"refusing to test against {conn.info.dbname!r}: not *_test")
        conn.execute("SELECT pg_advisory_lock(%s)", (TEST_LOCK,))
        database = Database(TEST_DATABASE_URL)
        database.migrate()
        yield database


@pytest.fixture
def database(_test_database):
    """The test database, empty: every person and everything they own gone."""
    with _test_database.connect() as conn:
        conn.execute("TRUNCATE users CASCADE")
    return _test_database
