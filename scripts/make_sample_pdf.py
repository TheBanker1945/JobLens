"""Turn a sample CV in markdown into a PDF, so the PDF path ships with the repo.

Mahdi's own CV is a PDF and nobody else's CV is in this repository, so without
this the PDF reader would only ever be exercised on a machine that already has a
real CV on it -- and `read_cv` would be the one part of JobLens that a person who
clones the project cannot run.

This is a deliberately dumb PDF writer: one column, one font, no layout. It exists
to produce a fixture, it is not part of the library, and a CV that comes out of
Word looks nothing like it. That is fine for what it proves -- that a text layer
is found, read and redacted -- and it costs no dependency to prove it.

Usage:
    uv run python scripts/make_sample_pdf.py data/samples/cvs/lisa_de_vries.md
"""

import argparse
import re
import sys
import textwrap
from pathlib import Path

FONT_SIZE = 10
LEADING = 14  # space between baselines
LEFT, TOP = 56, 790  # a 56pt margin on an A4 page (595 x 842pt)
LINES_PER_PAGE = 52
WRAP_AT = 92  # characters, at 10pt Helvetica this stays inside the margin

MARKDOWN = re.compile(r"^#{1,6}\s+|\*\*|\*|`")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="a .md or .txt CV")
    parser.add_argument("-o", "--out", type=Path, help="default: the source as .pdf")
    args = parser.parse_args()

    out = args.out or args.source.with_suffix(".pdf")
    lines = plain_lines(args.source.read_text(encoding="utf-8"))
    pages = [
        lines[start : start + LINES_PER_PAGE]
        for start in range(0, len(lines), LINES_PER_PAGE)
    ]
    out.write_bytes(build_pdf(pages))
    print(f"{out}: {len(pages)} page(s), {len(lines)} lines")
    return 0


def plain_lines(markdown: str) -> list[str]:
    """Markdown without its markers, wrapped to the page width."""
    lines: list[str] = []
    for raw in markdown.splitlines():
        stripped = MARKDOWN.sub("", raw).rstrip()
        if not stripped:
            lines.append("")
        else:
            lines.extend(textwrap.wrap(stripped, WRAP_AT) or [""])
    return lines


def build_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF 1.4 file: a catalog, a page tree, one font, one stream each.

    The tricky part of the format is the cross-reference table at the end, which
    lists the byte offset of every object. That is why the file is assembled as
    bytes here rather than as a string: the offsets have to be counted in the
    encoding the reader will see.
    """
    font_id = 3
    objects: dict[int, bytes] = {
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    }
    page_ids, next_id = [], font_id + 1
    for lines in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        stream = _content_stream(lines)
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (font_id, content_id)
        )
        objects[content_id] = b"<< /Length %d >>\nstream\n%s\nendstream" % (
            len(stream),
            stream,
        )
        page_ids.append(page_id)

    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])

    start_xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for number in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start_xref,
    )
    return bytes(out)


def _content_stream(lines: list[str]) -> bytes:
    """Text drawing commands: begin text, set the font, then one line at a time."""
    parts = [
        b"BT",
        b"/F1 %d Tf" % FONT_SIZE,
        b"%d TL" % LEADING,
        b"%d %d Td" % (LEFT, TOP),
    ]
    for line in lines:
        parts.append(b"(%s) Tj T*" % _escape(line))
    parts.append(b"ET")
    return b"\n".join(parts)


def _escape(line: str) -> bytes:
    """PDF strings are in brackets, so brackets and backslashes need escaping.

    WinAnsiEncoding is cp1252, which covers Dutch and Norwegian; anything outside
    it becomes a question mark rather than a broken file.
    """
    encoded = line.encode("cp1252", errors="replace")
    for char in (b"\\", b"(", b")"):
        encoded = encoded.replace(char, b"\\" + char)
    return encoded


if __name__ == "__main__":
    sys.exit(main())
