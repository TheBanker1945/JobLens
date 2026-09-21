"""What text do we embed for a vacancy?

An embedding compresses a whole text into one vector, so long texts dilute: in
milestone 2.1 the query "python baan in amsterdam" ranked a product manager above
the only Python job in Amsterdam, because "Python" was one word in a long story.

Three styles, compared in the 2.2 retrieval eval:
- raw:            the vacancy text as published (the 2.1 baseline)
- structured:     a short summary built from the extracted fields
- title_only:     only the job title (a deliberately minimal baseline)
- structured_raw: the summary followed by the full text

Extraction is lossy: "zzp'er" becomes contract_type=freelance and "geen diploma"
becomes education_level=null, so a summary drops the very words people search
with. structured_raw keeps both.
"""

import re
from typing import Literal

from joblens.extraction.schema import VacancyDetails

Style = Literal["raw", "structured", "title_only", "structured_raw"]
STYLES: tuple[Style, ...] = ("raw", "structured", "title_only", "structured_raw")


def build_document(
    text: str, details: VacancyDetails | None = None, style: Style = "raw"
) -> str:
    if style == "raw":
        return text
    if details is None:
        raise ValueError(f"style {style!r} needs extracted details")
    if style == "title_only":
        return details.title
    if style == "structured":
        return _structured(details)
    if style == "structured_raw":
        return f"{_structured(details)}\n\n{text}"
    raise ValueError(f"unknown style {style!r}")


def _structured(d: VacancyDetails) -> str:
    """Dutch labels: the vacancies are Dutch, so a Dutch summary stays closest to
    the language a Dutch job seeker searches in."""
    hours = _range(d.hours_min, d.hours_max)
    salary = _range(d.salary_min, d.salary_max)
    lines = [
        d.title,
        _line("Bedrijf", d.company),
        _line("Plaats", d.city),
        _line("Werkvorm", d.work_mode),
        _line("Uren per week", hours),
        _line("Dienstverband", d.contract_type),
        _line("Opleidingsniveau", d.education_level),
        _line(
            "Werkervaring",
            f"{d.experience_years_min} jaar" if d.experience_years_min else None,
        ),
        _line(
            "Salaris", f"{salary} per {d.salary_period}" if salary else d.salary_note
        ),
        _line("Vaardigheden", ", ".join(d.skills)),
        _line("Talen", ", ".join(d.languages_required)),
    ]
    return "\n".join(line for line in lines if line)


def _line(label: str, value) -> str | None:
    return f"{label}: {value}" if value else None


def _range(low, high) -> str | None:
    if low is None and high is None:
        return None
    if low is None:
        return f"tot {high:g}"
    if high is None or low == high:
        return f"{low:g}"
    return f"{low:g}-{high:g}"


# One vacancy is one vector today, which makes that vector the *average* of
# everything the advert says: the job, the team, the benefits and the company
# story. A query about one narrow part matches that average weakly. Chunking
# splits the text and lets a vacancy be scored by its best part instead.
#
# This is not about the model's context window -- measured in 2.4, no vacancy in
# the corpus comes close to being truncated. It is about dilution, which happens
# at every window size.
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150


def chunk_text(
    text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Split a text into overlapping pieces, on paragraph breaks where possible.

    Paragraphs are kept whole while they fit, because a paragraph is usually one
    idea and cutting mid-idea is what makes a chunk mean nothing. The overlap
    carries the tail of one chunk into the next, so a sentence that straddles a
    boundary still appears complete somewhere.

    `size` bounds the *new* text in a chunk; the carried-over tail sits on top of
    it, so a chunk holds at most `size + overlap` characters.
    """
    if size <= 0:
        raise ValueError("chunk size must be positive")
    if overlap >= size:
        raise ValueError("overlap must be smaller than the chunk size")

    pieces: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # A paragraph longer than a whole chunk has to be cut somewhere.
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", 0, size) or size
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[max(cut - overlap, 0) :].strip()
        if paragraph:
            pieces.append(paragraph)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            current = current[-overlap:].strip()
        current = f"{current}\n\n{piece}".strip() if current else piece
    if current:
        chunks.append(current)
    return chunks or [text.strip()]
