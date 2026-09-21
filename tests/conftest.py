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

import sys
from pathlib import Path

from joblens.extraction.schema import VacancyDetails
from joblens.llm.types import ChatResult, Usage

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
