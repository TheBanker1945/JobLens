"""A CV file -> plain text.

Two kinds of file, because a CV arrives as either:

- **PDF**, which is what everybody actually has. `pypdf` reads the text layer --
  the characters the PDF already contains. It does not read *pictures* of
  characters, so a scanned or photographed CV comes out empty. That is the one
  failure worth being loud about: an empty CV would otherwise sail through
  redaction and extraction and produce a confident, meaningless match list.
- **Plain text or markdown**, which is what the sample CVs in the repo are, so
  someone who clones JobLens can run the whole thing without owning a PDF.

Why pypdf: MIT, pure Python, no transitive dependencies, and a CV is one column
of text. Rejected: pdfplumber (pdfminer.six plus Pillow; better at columns and
tables -- worth adding the day a real CV needs it, not before), PyMuPDF (AGPL,
which is wrong for a public MIT repo), and OCR (a different problem, and the
error below tells the owner of a scanned CV what to do instead).
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

TEXT_SUFFIXES = frozenset({".txt", ".md"})
PDF_SUFFIX = ".pdf"

# A CV with fewer characters than this is not a CV. A scanned PDF usually
# extracts to nothing at all, but one with a text header over a scanned body
# yields a handful of characters, and that is the same failure.
MIN_USEFUL_CHARS = 200


class UnreadableCVError(Exception):
    """The file cannot be turned into CV text; the message says what to do."""


@dataclass(frozen=True)
class CVDocument:
    path: Path
    text: str
    pages: int  # 1 for a text file: it has no pages, and it is all one document

    @property
    def kind(self) -> str:
        return "pdf" if self.path.suffix.lower() == PDF_SUFFIX else "text"


def read_cv(path: Path) -> CVDocument:
    suffix = path.suffix.lower()
    if not path.exists():
        raise UnreadableCVError(f"No such file: {path}")
    if suffix == PDF_SUFFIX:
        return _read_pdf(path)
    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8")
        return CVDocument(path, _tidy(text), pages=1)
    supported = ", ".join(sorted(TEXT_SUFFIXES | {PDF_SUFFIX}))
    named = suffix or "a file with no extension"
    raise UnreadableCVError(f"Cannot read {named}: CVs are read from {supported}.")


def _read_pdf(path: Path) -> CVDocument:
    reader = PdfReader(path)
    if reader.is_encrypted:
        # An empty password covers the common "protected from editing" case.
        try:
            reader.decrypt("")
        except Exception as err:  # noqa: BLE001 - pypdf raises several types here
            raise UnreadableCVError(
                f"{path.name} is password-protected. Save an unprotected copy first."
            ) from err
    pages = [page.extract_text() or "" for page in reader.pages]
    text = _tidy("\n\n".join(pages))
    if len(text) < MIN_USEFUL_CHARS:
        raise UnreadableCVError(
            f"{path.name} has {len(text)} characters of text over {len(pages)} "
            "page(s), which means it holds a picture of a CV rather than a CV: a "
            "scan or an export that turned the text into an image. Export it from "
            "your editor as a text PDF, or save it as .txt or .md and point at that."
        )
    return CVDocument(path, text, pages=len(pages))


def _tidy(text: str) -> str:
    """The shape a CV comes out of a PDF in: hard line breaks, stray spacing.

    Only whitespace is touched. Nothing is joined or reflowed, because the line
    breaks in a CV carry its structure -- one job, one line -- and the model that
    reads it next does better with the layout intact than with a paragraph.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
