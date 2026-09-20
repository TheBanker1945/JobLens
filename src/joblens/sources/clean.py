"""Turn API payloads into plain text without personal data.

Two steps, both needed before anything is stored:
1. HTML -> readable text: vacancy APIs return career-page HTML, and an LLM does
   not need <div class="section"> to understand a job.
2. Remove contact details: a recruiter's name, email or phone is personal data
   under the GDPR, exactly like a CV. We never need it, so we never store it.
"""

import html
import re

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
PHONE = re.compile(
    r"(?<![\w-])(?:"
    r"\+\d{1,3}[\s.-]?(?:\(0\)[\s.-]?)?\d(?:[\s.-]?\d){6,11}"
    r"|0\d(?:[\s.-]?\d){7,10}"
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
