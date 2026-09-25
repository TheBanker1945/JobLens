"""Getting CV text out of the files people actually have."""

import logging

import pypdf
import pytest
from conftest import DAMAGED_CV, SAMPLE_CVS, build_pdf, scanned_pdf

from joblens.cv import read
from joblens.cv.read import (
    REFUSE_ABOVE,
    CVFile,
    UnreadableCVError,
    _collect_warnings,
    find_cv,
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


@pytest.mark.parametrize("name", ["lisa_de_vries.pdf", "lisa_de_vries.md"])
def test_an_upload_reads_exactly_as_the_file_it_came_from(name):
    """A browser hands over a name and bytes, not a path. Same text, same name."""
    path = SAMPLE_CVS / name

    from_disk = read_cv(path)
    uploaded = read_cv(CVFile(name, path.read_bytes()))

    assert uploaded.text == from_disk.text
    assert uploaded.pages == from_disk.pages
    assert uploaded.kind == from_disk.kind
    assert uploaded.path.stem == "lisa_de_vries"  # what labels and runs call it


def test_an_uploaded_file_name_is_never_a_place_on_disk():
    """Only the last part of the name counts, and it is read for its suffix."""
    with pytest.raises(UnreadableCVError, match="no extension"):
        read_cv(CVFile("../../.env", b"GEMINI_API_KEY=secret"))

    document = read_cv(CVFile("../../../etc/cv.md", b"# Jan\n" + b"Python. " * 40))
    assert str(document.path) == "cv.md"


def test_an_upload_keeps_line_ends_the_way_a_file_read_did():
    """Path.read_text turned Windows line ends into newlines; bytes do not."""
    windows = read_cv(CVFile("cv.txt", b"Jan\r\nPython\r\n"))

    assert "\r" not in windows.text


def test_text_that_is_not_utf8_is_refused_with_a_way_out():
    with pytest.raises(UnreadableCVError, match="not UTF-8"):
        read_cv(CVFile("cv.txt", "Curriculum vitae: café".encode("cp1252")))


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
        with _collect_warnings("pypdf") as collected:
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


def test_a_cv_is_found_by_the_name_a_labels_file_uses():
    found = find_cv("lisa_de_vries")

    assert found is not None
    assert found.name == "lisa_de_vries.md"  # text before PDF: no font to break


def test_a_pdf_is_found_when_it_is_the_only_copy(tmp_path):
    """The real CV is a PDF and nothing else. Skipping every .pdf is what kept
    the evals from ever reading it."""
    (tmp_path / "mahdi.pdf").write_bytes(b"%PDF-1.4\n")

    assert find_cv("mahdi", (tmp_path,)) == tmp_path / "mahdi.pdf"


def test_the_first_directory_wins_and_a_missing_one_is_skipped(tmp_path):
    real = tmp_path / "raw"
    real.mkdir()
    (real / "mahdi.md").write_text("x")

    assert find_cv("mahdi", (tmp_path / "gone", real)) == real / "mahdi.md"
    assert find_cv("nobody", (real,)) is None


def test_a_file_that_is_not_a_cv_format_is_not_offered(tmp_path):
    (tmp_path / "mahdi.docx").write_text("x")

    assert find_cv("mahdi", (tmp_path,)) is None


# ---------------------------------------------------------------------------
# A PDF whose words pypdf runs together (a real CV, 2026-09-24): XeLaTeX placed
# each word with a gap instead of a space, in a font that gave pypdf nothing to
# measure the gap against. That font could not be rebuilt in a hand-written
# fixture, so these tests glue pypdf's output themselves and let everything
# after it -- pdfminer.six included, on the committed sample PDF -- run for real.

SAMPLE_PDF = SAMPLE_CVS / "lisa_de_vries.pdf"


def glue_pypdf(monkeypatch):
    """pypdf's reading of every page, with the spaces inside each line gone."""
    original = pypdf.PageObject.extract_text

    def glued(page, *args, **kwargs):
        text = original(page, *args, **kwargs)
        return "\n".join(line.replace(" ", "") for line in text.splitlines())

    monkeypatch.setattr(pypdf.PageObject, "extract_text", glued)


def test_a_pdf_that_reads_fine_is_read_once(monkeypatch):
    def second_read(path):
        raise AssertionError("pdfminer was asked about a PDF pypdf read fine")

    monkeypatch.setattr(read, "_read_with_pdfminer", second_read)

    document = read_cv(SAMPLE_PDF)

    assert document.reader == "pypdf"
    assert document.reading_note() is None


def test_glued_text_is_read_again_and_the_better_reading_kept(monkeypatch):
    glue_pypdf(monkeypatch)

    document = read_cv(SAMPLE_PDF)

    assert document.reader == "pdfminer.six"
    assert "Data-analist — Coolblue, Rotterdam" in document.text  # spaced, as shown
    assert document.glued > read.GLUED_ABOVE
    assert "pdfminer.six" in document.reading_note()


def test_a_glyph_pdfminer_cannot_map_is_a_marked_hole(monkeypatch):
    glue_pypdf(monkeypatch)
    monkeypatch.setattr(
        read,
        "extract_text",
        lambda path, laparams: (
            "(cid:294) (+31) Utrecht\n"
            + "Werkervaring bij Coolblue in Rotterdam\n" * 20
        ),
    )

    document = read_cv(SAMPLE_PDF)

    assert document.text.startswith("? (+31) Utrecht")
    assert document.damage.glyphs == 1


@pytest.mark.parametrize(
    ("first", "second", "better"),
    [
        (
            "Developmentofwebshopsforclientsinretail " * 5,
            "Development of webshops " * 5,
            True,
        ),
        (
            "A CV that reads fine, word by word. " * 5,
            "A CV that reads fine " * 5,
            False,
        ),
        # less glued, but not by half: a second reader is not better for trying
        (
            "averyverylongwordinsidealine and more " * 5,
            "averyverylongwordinsidealine and " * 5,
            False,
        ),
    ],
)
def test_the_second_reading_is_kept_only_when_clearly_less_glued(first, second, better):
    assert read._better_reading(first, second) is better
