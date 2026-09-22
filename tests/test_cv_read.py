"""Getting CV text out of the files people actually have."""

import logging

import pytest
from conftest import DAMAGED_CV, SAMPLE_CVS, build_pdf, scanned_pdf

from joblens.cv.read import (
    REFUSE_ABOVE,
    UnreadableCVError,
    _collect_pypdf_warnings,
    read_cv,
)


def test_reads_a_markdown_cv():
    document = read_cv(SAMPLE_CVS / "lisa_de_vries.md")

    assert document.kind == "text"
    assert "Data-analist" in document.text
    assert document.pages == 1


def test_reads_the_committed_pdf_including_its_second_page():
    document = read_cv(SAMPLE_CVS / "lisa_de_vries.pdf")

    assert document.kind == "pdf"
    assert document.pages == 2
    assert "Coolblue" in document.text  # page one
    assert "Vaardigheden" in document.text  # page two


def test_the_pdf_and_the_markdown_say_the_same_things():
    """Not byte for byte -- markdown markers and wrapping differ -- but the facts
    have to survive the trip through the PDF, or the sample proves nothing."""
    text = read_cv(SAMPLE_CVS / "lisa_de_vries.pdf").text

    for fact in ("Power BI", "Hogeschool Utrecht", "Gemeente Utrecht", "dbt"):
        assert fact in text


def test_a_scan_is_refused_rather_than_read_as_an_empty_cv(tmp_path):
    """A PDF with no text layer extracts to nothing, and an empty CV would
    otherwise produce a confident, meaningless list of matches."""
    path = tmp_path / "scan.pdf"
    path.write_bytes(scanned_pdf())

    with pytest.raises(UnreadableCVError, match="picture of a CV"):
        read_cv(path)


def test_an_unsupported_file_type_says_what_is_supported(tmp_path):
    path = tmp_path / "cv.docx"
    path.write_text("x")

    with pytest.raises(UnreadableCVError, match=r"\.md, \.pdf, \.txt"):
        read_cv(path)


def test_a_missing_file_is_reported_as_missing(tmp_path):
    with pytest.raises(UnreadableCVError, match="No such file"):
        read_cv(tmp_path / "nope.pdf")


def test_whitespace_is_tidied_but_lines_are_kept(tmp_path):
    path = tmp_path / "cv.txt"
    path.write_text("Titel   \r\n\n\n\n  Werkervaring\ttwee   spaties\n", "utf-8")

    text = read_cv(path).text

    assert text == "Titel\n\nWerkervaring twee spaties"


def test_unreadable_glyphs_are_counted_and_marked(tmp_path):
    """The bug a real CV found: a PDF whose font maps characters into the
    private use area has a text layer that does not say what the page shows.
    They must not be silently dropped -- `Dec   Sep` is what made a model invent
    a pair of dates -- so each one becomes a visible hole."""
    path = tmp_path / "damaged.txt"
    path.write_text(DAMAGED_CV, encoding="utf-8")

    document = read_cv(path)

    assert "" not in document.text
    assert "Dec ???? ? Sep ????" in document.text
    assert document.damage.glyphs == 23
    assert document.damage.distinct == 9
    assert document.damage.lines == 6
    assert 0 < document.damage.share < REFUSE_ABOVE


def test_a_damaged_cv_says_which_lines_are_damaged(tmp_path):
    """The warning has to be readable, or it is not a warning."""
    path = tmp_path / "damaged.txt"
    path.write_text(DAMAGED_CV, encoding="utf-8")

    report = "\n".join(read_cv(path).damage.report())

    assert "23 unreadable characters" in report
    assert "Backend Developer Dec ???? ? Sep ????" in report


def test_an_undamaged_cv_reports_no_damage():
    document = read_cv(SAMPLE_CVS / "lisa_de_vries.pdf")

    assert not document.damage
    assert document.damage.report() == []


def test_a_cv_that_is_mostly_glyphs_is_refused_like_a_scan(tmp_path):
    """Marking the holes is only honest while there is a CV around them."""
    path = tmp_path / "gibberish.txt"
    path.write_text(("Naam: Jan Bakker\n" + "" * 60) * 4, encoding="utf-8")

    with pytest.raises(UnreadableCVError, match="unreadable"):
        read_cv(path)


def test_a_pdf_of_glyphs_is_a_picture_of_a_cv(tmp_path):
    """Readable characters are counted without the glyphs, so a page of them is
    as empty as a blank one and gets the same advice."""
    path = tmp_path / "glyphs.pdf"
    path.write_bytes(build_pdf([["Jan Bakker"]]))

    with pytest.raises(UnreadableCVError, match="picture of a CV"):
        read_cv(path)


def test_pypdf_warnings_are_collected_instead_of_printed():
    """A broken font makes pypdf log once per malformed entry -- 64 lines before
    the first word of output on the CV that found this. They are counted here and
    reported as one line, and they stop reaching whatever handles logging."""
    seen: list[logging.LogRecord] = []
    root = logging.getLogger()
    root.addHandler(_Record(seen))
    try:
        with _collect_pypdf_warnings() as collected:
            logging.getLogger("pypdf._cmap").warning("Skipping broken line")
        logging.getLogger("pypdf._cmap").warning("after the read")
    finally:
        root.handlers.pop()

    assert [record.getMessage() for record in collected] == ["Skipping broken line"]
    assert [record.getMessage() for record in seen] == ["after the read"]


class _Record(logging.Handler):
    """Stands in for whatever the person running this has logging set up to do."""

    def __init__(self, into: list[logging.LogRecord]):
        super().__init__()
        self.into = into

    def emit(self, record: logging.LogRecord) -> None:
        self.into.append(record)
