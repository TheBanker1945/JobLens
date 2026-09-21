"""Getting CV text out of the files people actually have."""

import pytest
from conftest import SAMPLE_CVS, scanned_pdf

from joblens.cv.read import UnreadableCVError, read_cv


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
