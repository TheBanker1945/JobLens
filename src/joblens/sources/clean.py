"""Turn API payloads into plain text without personal data.

Two steps, both needed before anything is stored:
1. HTML -> readable text: vacancy APIs return career-page HTML, and an LLM does
   not need <div class="section"> to understand a job.
2. Remove contact details: a recruiter's name, email or phone is personal data
   under the GDPR, exactly like a CV. We never need it, so we never store it.
"""

import html
import re
from collections.abc import Callable
from html.parser import HTMLParser

BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|tr|section|article)>", re.I)
BREAK = re.compile(r"<(br|hr)\s*/?>", re.I)
LIST_ITEM = re.compile(r"<li[^>]*>", re.I)
TAG = re.compile(r"<[^>]+>")
BLANK_LINES = re.compile(r"\n{3,}")
SPACES = re.compile(r"[ \t]{2,}")

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Phone numbers in two shapes, kept narrow so salary ranges ("3200 - 3800") and
# years are never touched:
#   international, country code then digits: +31 6 12345678, +31 (0)70 700 0510
#   national, always starting with 0:        06-12345678, 070 7000510
#
# Up to three characters may separate two digits, because people write a number
# with spaces around the dash ("06 - 3318 2245", found on a sample CV in 3.4).
# That stays safe because both shapes must begin with a 0 or a country code: a
# range like "3200 - 3800" has neither, and neither does a year.
SEPARATOR = r"[\s.-]{0,3}"
PHONE = re.compile(
    r"(?<![\w-])(?:"
    rf"\+\d{{1,3}}{SEPARATOR}(?:\(0\){SEPARATOR})?\d(?:{SEPARATOR}\d){{6,11}}"
    rf"|0\d(?:{SEPARATOR}\d){{7,10}}"
    r")(?![\w-])"
)


def html_to_text(raw: str) -> str:
    text = BREAK.sub("\n", raw)
    text = LIST_ITEM.sub("\n- ", text)
    text = BLOCK_END.sub("\n", text)
    text = TAG.sub("", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")  # &nbsp; unescapes to a non-breaking space
    lines = [SPACES.sub(" ", line).strip() for line in text.splitlines()]
    return BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def strip_contact_details(text: str) -> str:
    """Replace emails and phone numbers with a placeholder (GDPR)."""
    text = EMAIL.sub("[email removed]", text)
    return PHONE.sub("[phone removed]", text)


def to_clean_text(raw: str) -> str:
    return strip_contact_details(html_to_text(raw))


def redact(payload):
    """Remove contact details from every string in a payload, however deep.

    A `Vacancy` keeps the original payload in `raw`, and that payload holds the
    description a second time, in HTML, with the recruiter's e-mail still in it.
    Redacting the text but storing the payload untouched would put the personal
    data back on disk, so the same rule applies to both.
    """
    if isinstance(payload, str):
        return strip_contact_details(payload)
    if isinstance(payload, dict):
        return {key: redact(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [redact(item) for item in payload]
    return payload


# Void elements never get a closing tag, so they must not count towards the depth.
VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


def extract_by_class(raw: str, class_name: str) -> str | None:
    """The inner HTML of the first element carrying `class_name`, or None.

    LinkedIn returns a whole page fragment where only one div holds the vacancy
    text. Written with the standard library's HTMLParser rather than an HTML
    library: it is one element we need, matched on one class, and the parser is
    already there.
    """
    found = extract_elements(
        raw, lambda attrs: class_name in (attrs.get("class") or "").split(), first=True
    )
    return found[0] if found else None


def extract_elements(
    raw: str, match: Callable[[dict[str, str | None]], bool], *, first: bool = False
) -> list[str]:
    """The inner HTML of every element whose attributes `match` accepts.

    werkenbijdeoverheid.nl splits a vacancy over six <section id="..._anchor">
    elements ("Dit ga je doen", "Dit vragen wij", ...), so one element is not
    enough there. An element inside a matching element is part of it, not a
    second match.
    """
    picker = _ElementPicker(match, first)
    picker.feed(raw)
    picker.close()
    return ["".join(parts) for parts in picker.found]


class _ElementPicker(HTMLParser):
    """Copies everything between an element's start and end tag, nesting included."""

    def __init__(self, match: Callable[[dict[str, str | None]], bool], first: bool):
        super().__init__(convert_charrefs=True)
        self.match = match
        self.first = first  # stop at the first match
        self.found: list[list[str]] = []
        self.depth = 0  # 0 = not inside an element we want

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth == 0:
            if (self.first and self.found) or not self.match(dict(attrs)):
                return
            self.found.append([])
            self.depth = 1
            return  # the element's own start tag is not part of its inner HTML
        if tag not in VOID_TAGS:
            self.depth += 1
        self.found[-1].append(self.get_starttag_text() or f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if self.depth == 0 or tag in VOID_TAGS:
            return
        self.depth -= 1
        if self.depth > 0:  # the element's own end tag closes it, and is dropped
            self.found[-1].append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.depth > 0:
            self.found[-1].append(data)
