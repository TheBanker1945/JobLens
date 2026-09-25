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

**pdfminer.six, second, since 2026-09-24.** One of ten real CVs came out of pypdf
as `Developmentofwebshopsforclients...`: XeLaTeX placed each word with a gap rather
than a space character, in a font that gives pypdf no space width to measure the
gap against. pdfminer.six works out spaces from the gaps between glyphs and reads
it cleanly, with each job's lines kept together. It is only a second opinion:
pypdf still reads every CV, and pdfminer's text is used only when pypdf's is
glued *and* pdfminer's is clearly less so -- because on the real CV that has
always read fine, pdfminer's text is the worse of the two. pdfplumber, which
sits on pdfminer, glued the same file as pypdf did.

**The third failure, found on a real CV in 3.6.1: a PDF that has a text layer and
still does not say what it displays.** Some exporters subset a font and write a
`ToUnicode` map that points at the *private use area* -- `<0551> <0555> <E073>`
-- so the file itself declares "these characters have no Unicode meaning". The
text comes out as `Dec \\ue073\\ue071\\ue073\\ue075`, which is `Dec 2024` on
screen and nothing at all on disk. No reader can recover it, because it is not
there: only the glyph outlines know, and reading those is OCR.

That is dangerous in a way an empty page is not. It is *mostly* readable, so it
passes every check, and the holes land in the shortest, most factual things on a
CV -- years, brackets, a "+" in front of a phone number. A model handed
`Founder & Full-Stack Developer Dec   Sep` does not report a hole; it writes a
plausible pair of dates. So the unreadable characters are never silently dropped:
they are counted, reported, and replaced with `?`, which is a hole the next step
can see and refuse to fill.

**A path or an upload (7.1).** A script names a file on disk; a browser hands
over a file name and its bytes. Everything below reads bytes, so the two are one
code path and a CV reads the same whichever way it arrived.
"""

import io
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
from pypdf import PdfReader

TEXT_SUFFIXES = frozenset({".txt", ".md"})
PDF_SUFFIX = ".pdf"

# src/joblens/cv/read.py -> src/joblens/cv -> src/joblens -> src -> the repo root.
ROOT = Path(__file__).resolve().parents[3]

# Where a CV named in a labels file actually lives. The samples are committed and
# invented; data/raw/cv/ is gitignored and holds the real one. Both are searched,
# because an eval reads a name ("mohammed") rather than a path.
CV_DIRECTORIES = (
    ROOT / "data" / "samples" / "cvs",
    ROOT / "data" / "raw" / "cv",
)

# A CV with fewer characters than this is not a CV. A scanned PDF usually
# extracts to nothing at all, but one with a text header over a scanned body
# yields a handful of characters, and that is the same failure.
MIN_USEFUL_CHARS = 200

# The three private use areas: the basic one every broken font subset lands in,
# and the two supplementary planes, which no CV has a legitimate use for either.
PRIVATE_USE = re.compile(r"[-\U000f0000-\U000ffffd\U00100000-\U0010fffd]")

# What an unreadable character becomes. Not deletion: `Dec   Sep` invites the
# model to invent the year, `Dec ???? ? Sep ????` does not. One mark per glyph,
# so the size of the hole is visible too.
UNREADABLE = "?"

# Above this share of the text, a file is not a damaged CV but an unreadable one,
# and it is refused for the same reason a scan is. Deliberately far above the
# 1.3% that broke a real CV: below the line the text is still worth reading, and
# the safety comes from marking the holes rather than from the threshold.
REFUSE_ABOVE = 0.10

# A "word" this long is almost always words run together: across ten real CVs
# and five others, at most 1.6% of the words were, and those were links. On
# the CV whose PDF places words without spaces, 40% were; on one where only
# some lines do, 3.6%.
GLUED_WORD = 20
# Above this share pypdf's text is read a second time, with pdfminer.six. A
# false alarm costs one more read and changes nothing, because the second text
# is used only if it at least halves the share (`_better_reading`).
GLUED_ABOVE = 0.02

# What pdfminer writes for a glyph with no Unicode meaning: "(cid:294)". It is
# the same hole as a private-use character, so it becomes one and is counted,
# marked and reported by the same code.
CID = re.compile(r"\(cid:\d+\)")

# How many damaged lines the warning prints. Enough to recognise which parts of
# the CV are gone, few enough to stay a warning rather than a second copy of it.
EXAMPLE_LINES = 6


class UnreadableCVError(Exception):
    """The file cannot be turned into CV text; the message says what to do."""


@dataclass(frozen=True)
class CVFile:
    """A CV as an upload hands it over: a file name and its bytes.

    The name is only ever read for its suffix, which picks the reader, and its
    stem, which names the CV. It is never a place on disk: a browser that sends
    "../../.env" as a file name gets a CV called ".env", refused for having no
    suffix a CV can have, and nothing is opened.
    """

    name: str
    data: bytes = field(repr=False)  # a CV is personal data; keep it out of logs


@dataclass(frozen=True)
class Damage:
    """What a file displays but does not contain, counted before it is marked."""

    glyphs: int = 0  # unreadable characters in total
    distinct: int = 0  # how many different ones: one broken font, or several
    lines: int = 0  # lines holding at least one
    total_lines: int = 0
    chars: int = 0  # length of the text they were found in
    reader_warnings: int = 0  # what pypdf complained about while reading
    examples: tuple[str, ...] = ()  # damaged lines, already marked

    def __bool__(self) -> bool:
        return self.glyphs > 0

    @property
    def share(self) -> float:
        return self.glyphs / self.chars if self.chars else 0.0

    def report(self) -> list[str]:
        """The warning, as lines to print. Empty when nothing is wrong."""
        if not self:
            return []
        out = [
            f"!! {self.glyphs} unreadable characters ({self.distinct} different) "
            f"on {self.lines} of {self.total_lines} lines, {self.share:.1%} of the "
            "text.",
            "   This file's font maps them to private codes, so the file does not "
            "contain",
            "   what it displays and nothing can read them back. They are sent as "
            '"?", and',
            "   a date that rests on one is dropped rather than guessed. Re-export "
            "the PDF",
            "   (no font subsetting), print it to PDF, or save it as .txt if these "
            "lines matter:",
        ]
        out += [f"     {line}" for line in self.examples]
        return out


@dataclass(frozen=True)
class CVDocument:
    path: Path  # where it was read from, or just the file name for an upload
    text: str
    pages: int  # 1 for a text file: it has no pages, and it is all one document
    damage: Damage = field(default_factory=Damage)
    reader: str = "pypdf"  # or "pdfminer.six", when pypdf's text was glued
    glued: float = 0.0  # the share of run-together words in pypdf's text

    def reading_note(self) -> str | None:
        """One line when the second reader was used, so the switch is visible."""
        if self.reader == "pypdf":
            return None
        return (
            f"note: pypdf ran {self.glued:.0%} of this PDF's words together (it "
            "places words without spaces), so it was read with pdfminer.six."
        )

    @property
    def kind(self) -> str:
        return "pdf" if self.path.suffix.lower() == PDF_SUFFIX else "text"


def read_cv(source: Path | CVFile) -> CVDocument:
    if isinstance(source, CVFile):
        # The last part only: whatever directories a browser put in front of
        # the name, this reads from memory and names the CV after the file.
        path = Path(Path(source.name).name)
    else:
        path = source
        if not path.exists():
            raise UnreadableCVError(f"No such file: {path}")
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES | {PDF_SUFFIX}:
        supported = ", ".join(sorted(TEXT_SUFFIXES | {PDF_SUFFIX}))
        named = suffix or "a file with no extension"
        raise UnreadableCVError(f"Cannot read {named}: CVs are read from {supported}.")
    data = source.data if isinstance(source, CVFile) else path.read_bytes()
    if suffix == PDF_SUFFIX:
        return _read_pdf(path, data)
    return _document(path, _decode(path, data), pages=1)


def _decode(path: Path, data: bytes) -> str:
    """A text file's bytes as `Path.read_text` would have read them."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as err:
        raise UnreadableCVError(
            f"{path.name} is not UTF-8 text. Save it as UTF-8, or export a PDF."
        ) from err
    # Text mode turns Windows and old Mac line ends into "\n"; bytes do not.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_pdf(path: Path, data: bytes) -> CVDocument:
    with _collect_warnings("pypdf") as warnings:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # An empty password covers the common "protected from editing" case.
            try:
                reader.decrypt("")
            except Exception as err:  # noqa: BLE001 - pypdf raises several types
                raise UnreadableCVError(
                    f"{path.name} is password-protected. Save an unprotected copy "
                    "first."
                ) from err
        pages = [page.extract_text() or "" for page in reader.pages]
    text = _tidy("\n\n".join(pages))
    glued, reader = glued_share(text), "pypdf"
    if glued > GLUED_ABOVE:
        second = _read_with_pdfminer(data)
        if _better_reading(text, second):
            text, reader = second, "pdfminer.six"
    # Counted without the glyphs: a page of characters that carry no meaning is
    # as empty as a page with nothing on it.
    unreadable = len(PRIVATE_USE.findall(text))
    readable = len(text) - unreadable
    if readable < MIN_USEFUL_CHARS:
        glyphs = f" ({unreadable} more are unreadable glyphs)" if unreadable else ""
        raise UnreadableCVError(
            f"{path.name} has {readable} characters of readable text over "
            f"{len(pages)} page(s){glyphs}, which means it holds a picture of a CV "
            "rather than a CV: a scan, or an export that turned the text into an "
            "image. Export it from your editor as a text PDF, or save it as .txt "
            "or .md and point at that."
        )
    document = _document(path, text, pages=len(pages), reader_warnings=len(warnings))
    return replace(document, reader=reader, glued=glued)


def glued_share(text: str) -> float:
    """The share of "words" long enough to be several run together."""
    words = text.split()
    return sum(len(word) > GLUED_WORD for word in words) / len(words) if words else 0.0


def _better_reading(first: str, second: str) -> bool:
    """Whether the second reading is clearly less glued than the first: by at
    least half, and never when the first was not glued to begin with."""
    before = glued_share(first)
    return before > 0 and glued_share(second) <= before / 2


def _read_with_pdfminer(data: bytes) -> str:
    """pdfminer.six's reading, with its unmapped glyphs made private-use.

    `boxes_flow=None` turns off its column detection, which otherwise moves a
    job's dates away from its title; lines are then read top to bottom.
    """
    with _collect_warnings("pdfminer"):
        raw = extract_text(io.BytesIO(data), laparams=LAParams(boxes_flow=None))
    return _tidy(CID.sub("\ue000", raw))


def _document(
    path: Path, text: str, *, pages: int, reader_warnings: int = 0
) -> CVDocument:
    """Tidy, measure the damage, mark it, and refuse a file that is mostly holes."""
    text = _tidy(text)
    marked = PRIVATE_USE.sub(UNREADABLE, text)
    damage = _assess(text, marked, reader_warnings)
    if damage.share > REFUSE_ABOVE:
        raise UnreadableCVError(
            f"{path.name} is {damage.share:.0%} unreadable: {damage.glyphs} of its "
            f"{damage.chars} characters are private-use glyphs, on {damage.lines} of "
            f"{damage.total_lines} lines. The font in this file maps its characters "
            "to private codes, so the file does not contain what it displays and no "
            "reader can recover it. Export it again without font subsetting, print "
            "it to PDF from a viewer, or save it as .txt or .md and point at that."
        )
    return CVDocument(path, marked, pages=pages, damage=damage)


def _assess(text: str, marked: str, reader_warnings: int) -> Damage:
    found = PRIVATE_USE.findall(text)
    lines, marked_lines = text.split("\n"), marked.split("\n")
    damaged = [
        shown
        for original, shown in zip(lines, marked_lines, strict=True)
        if PRIVATE_USE.search(original)
    ]
    return Damage(
        glyphs=len(found),
        distinct=len(set(found)),
        lines=len(damaged),
        total_lines=len(lines),
        chars=len(text),
        reader_warnings=reader_warnings,
        examples=tuple(damaged[:EXAMPLE_LINES]),
    )


@contextmanager
def _collect_warnings(name: str) -> Iterator[list[logging.LogRecord]]:
    """Catch a PDF reader's log lines instead of letting them flood the terminal.

    A PDF with a broken font subset makes pypdf warn once per malformed CMap
    entry -- 64 lines of `Skipping broken line b'07ac 07b2 1f130'` before the
    first word of output on a real CV. They matter as a *count*, which is a
    symptom of the same problem `Damage` describes, so they are collected here
    and reported as one line by whoever asked for the CV.
    """
    logger = logging.getLogger(name)
    collected: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            collected.append(record)

    handler = Collect()
    propagate = logger.propagate
    logger.addHandler(handler)
    logger.propagate = False  # keep them off the root handler for this read only
    try:
        yield collected
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate


def _tidy(text: str) -> str:
    """The shape a CV comes out of a PDF in: hard line breaks, stray spacing.

    Only whitespace is touched. Nothing is joined or reflowed, because the line
    breaks in a CV carry its structure -- one job, one line -- and the model that
    reads it next does better with the layout intact than with a paragraph.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = text.replace("\f", "\n")  # pdfminer ends every page with a form feed
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def find_cv(name: str, directories: tuple[Path, ...] = CV_DIRECTORIES) -> Path | None:
    """The file a CV is named after, or None.

    Text before PDF when both exist: `lisa_de_vries.md` and `lisa_de_vries.pdf`
    are the same CV, and the markdown has no font to be broken. A PDF is still
    found when it is the only copy -- which is the normal case for a real CV, and
    was a bug until 2026-09-22: the eval scripts skipped every .pdf and could
    therefore never read the one CV that matters.
    """
    for directory in directories:
        if not directory.is_dir():
            continue
        found = sorted(
            path
            for path in directory.glob(f"{name}.*")
            if path.suffix.lower() in TEXT_SUFFIXES | {PDF_SUFFIX}
        )
        text_first = [p for p in found if p.suffix.lower() != PDF_SUFFIX]
        if text_first or found:
            return (text_first or found)[0]
    return None
