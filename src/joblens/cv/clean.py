"""Remove from a CV what matching does not need.

A vacancy is public and a CV is not, so this goes further than
`sources/clean.py`, which strips a recruiter's e-mail and phone number from an
advert. The rule from the brief: only send what the task needs. A phone number,
a street address and a date of birth cannot make a job a better or worse fit, so
they never leave the machine.

Three things are deliberately *kept*:

- **The city.** It decides whether a job is commutable, which is exactly the
  kind of thing matching is for. A street and a postcode are not.
- **Nationality, and anything about work permits.** It reads like personal data
  and it is, but it can be a hard requirement in a vacancy, so removing it would
  make the answer wrong rather than private.
- **The name**, unless the caller passes one. Finding a name in free text needs
  either a model -- an LLM call to hide data from an LLM call -- or a guess, and
  a wrong guess deletes a skill or a company instead. `redact_cv(text,
  name="...")` removes an exact name with no guessing, and the script that calls
  this says so when no name was given.

Every removal is reported rather than silently applied: `Redacted.removals` is
what was taken out, so a person can check for a false positive before anything
is sent. That list holds the original values, so it is for the terminal, never
for a file that gets committed.
"""

import re
from dataclasses import dataclass

from joblens.sources.clean import EMAIL, PHONE, SEPARATOR

# A Dutch postcode: "1017 AB". Four digits never starting at zero, then two
# letters. Three restrictions, all earned by a false positive on a real CV:
#
# - the space may not be a line break, or "augustus 2023\nAd-hoc analyses" is a
#   postcode;
# - the letters may not run into a hyphenated word ("2023 Ad-hoc");
# - the letters may not be one of the little lowercase words that follow a year
#   in ordinary prose ("sinds 2019 en 2021"). Only lowercase: "1016 EN" is a real
#   Amsterdam postcode and stays covered, while "en" in a sentence does not.
NOT_A_POSTCODE = (
    "en in op te de ze we je ik of af na om zo al aan as at to is an by on or it "
    "be do no so up my me"
).split()
# A fourth: a *year* followed by one of the two-letter acronyms a CV puts after
# a year -- "2019 - 2021 IT Consultant", "Sinds 2023 AI engineer". Read as a
# postcode, the year and the skill both went (found in the 2026-09-22 audit).
# Only for year-shaped numbers, because 1900-2099 are real postcodes around
# Haarlem and IJmuiden: "2021 AI" there is missed, which costs the four digits
# of an area whose city the CV keeps on purpose anyway. The street is still
# caught by STREET.
ACRONYMS_AFTER_A_YEAR = (
    "IT AI ML QA UX UI BI HR PM PO VP BA MA BS MS NL EU UK US BV"
).split()
POSTCODE = re.compile(
    r"\b(?!(?:19|20)\d{2} ?(?:" + "|".join(ACRONYMS_AFTER_A_YEAR) + r")\b)"
    r"[1-9]\d{3} ?(?!(?:" + "|".join(NOT_A_POSTCODE) + r")\b)[A-Za-z]{2}\b(?!-)"
)

# A street line: a word ending in a Dutch street suffix, then a house number.
# Matching the suffix rather than a capital letter keeps "Java 8, Spring Boot 3"
# out of it.
#
# Two limits, both from the 2026-09-22 audit, and both the postcode's lessons
# again. The number has to be on the same line: "Loopbaan" as a heading with
# "2019 - 2023" under it was an address. And the number may not be a year:
# "baan" is also the Dutch word for a job, so "Bijbaan 2018 - 2020" and
# "Gerechtshof 2018" were addresses too. A real house number between 1900 and
# 2099 is missed by this; the postcode on the same line is not.
STREET = re.compile(
    r"\b[A-Za-zÀ-ſ'.-]*"
    r"(?:straat|laan|weg|plein|kade|dijk|singel|gracht|hof|pad|dreef|steeg|baan|"
    r"boulevard|park)"
    r"[ \t]+(?!(?:19|20)\d{2}\b)\d+[ \t]*[a-zA-Z]?\b",
    re.I,
)

# "Geboortedatum: 3 maart 1995" -- the label and the rest of the line.
BIRTH_LINE = re.compile(
    r"(?im)^.*\b(geboortedatum|geboortedag|geboren(?:\s+op)?|date\s+of\s+birth|"
    r"birth\s*date|dob)\b.*$"
)
# A full day-month-year date. Work history is written in years or months
# ("2019 - 2023", "maart 2021"), so a complete date in a CV is almost always a
# date of birth -- and on the rare occasion it is a project date, losing it
# costs nothing for matching.
FULL_DATE = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}\b")

URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
# A profile link written without a scheme, e.g. "linkedin.com/in/jane-doe".
BARE_PROFILE = re.compile(
    r"\b(?:linkedin|github|gitlab|instagram|facebook|x)\.com/\S+", re.I
)

# An IBAN is unmistakable and never belongs on a CV.
IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z]{4}\d{10}\b")

# A phone number whose "+" did not survive the PDF. On a real CV the "+" of
# "+31 6 ..." was a private-use glyph (see cv/read.py), so `PHONE` -- which
# needs a "+" or a leading "0" -- matched nothing and the number was sent to a
# cloud model. This rule needs neither.
#
# It is anchored on an explicit calling code rather than "one to three digits",
# because a generic prefix turns a row of years ("2021 2022 2023 2024") into a
# phone number. The list is short and Dutch-first, and it is a known limit in
# the same way Ingrid Solheim's Norwegian address is: a number from a country
# not listed here is caught only if it is written with its "+" or as one run of
# digits.
CALLING_CODES = ("31", "32", "33", "44", "47", "49")
# Nine digits after the code, not eight, and that is what keeps a salary range
# out: "32.000 - 38.000" starts with a calling code and has eight.
COUNTRY_CODE_PHONE = re.compile(
    r"(?<![\w+])(?:00" + SEPARATOR + r")?"
    r"(?:"
    + "|".join(CALLING_CODES)
    + r")"
    + SEPARATOR
    + r"(?:\(0\)"
    + SEPARATOR
    + r")?"
    r"\d(?:" + SEPARATOR + r"\d){8,11}(?![\w-])"
)

# Eight or more digits in a row. A burgerservicenummer is nine of them, a phone
# number written solid is ten, and a bank or customer number is whatever it is:
# a CV has no honest use for a run this long, and the removal list shows what
# went if a very large figure ever matches.
DIGIT_RUN = re.compile(r"(?<![\w-])\d{8,}(?![\w-])")

# Order matters: a URL is removed before the e-mail inside it can be, and the
# birth *line* before the date on it, so the removal is reported as one thing.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url", URL),
    ("url", BARE_PROFILE),
    ("email", EMAIL),
    ("iban", IBAN),
    ("date of birth", BIRTH_LINE),
    ("date of birth", FULL_DATE),
    # The country-code rule goes first: "0031 70 700 0510" starts with a zero,
    # so PHONE matches it as a national number and stops at the first place it
    # can, leaving the last block on the page.
    ("phone", COUNTRY_CODE_PHONE),
    ("phone", PHONE),
    ("long number", DIGIT_RUN),
    ("address", STREET),
    ("postcode", POSTCODE),
)


@dataclass(frozen=True)
class Removal:
    kind: str
    original: str


@dataclass(frozen=True)
class Redacted:
    text: str
    removals: list[Removal]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for removal in self.removals:
            counts[removal.kind] = counts.get(removal.kind, 0) + 1
        return counts


def redact_cv(text: str, *, name: str | None = None) -> Redacted:
    """The text that is safe to send, and everything that was taken out of it."""
    removals: list[Removal] = []
    for kind, pattern in RULES:
        text = _remove(text, pattern, kind, removals)
    if name:
        for pattern in _name_patterns(name):
            text = _remove(text, pattern, "name", removals)
    return Redacted(_tidy(text), removals)


def _remove(
    text: str, pattern: re.Pattern[str], kind: str, removals: list[Removal]
) -> str:
    def replace(match: re.Match[str]) -> str:
        removals.append(Removal(kind, match.group(0).strip()))
        return f"[{kind} removed]"

    return pattern.sub(replace, text)


# The small words inside a Dutch surname. "de" was already safe for being two
# letters; "van" and "der" are three, so until the 2026-09-22 audit a name like
# "Jan van der Berg" removed every "van" and "der" in the CV -- "ontwikkeling
# van dashboards" included.
NAME_PARTICLES = frozenset(
    "van der den het ter ten von vom zum zur del dos das".split()
)

# A name part followed by a year is a date, not a name: the first name "Jan"
# must not take "Jan 2021" with it.
BEFORE_A_YEAR = r"(?!\.?[ \t]*(?:(?:19|20)\d{2}|'\d{2})\b)"


def _name_patterns(name: str) -> list[re.Pattern[str]]:
    """The whole name first, then each part, so "Jan de Vries" does not survive
    as "Vries" further down the page. Initials, two-letter words and the
    particles above are left alone: they match too much of an ordinary CV."""
    parts = [
        part
        for part in name.split()
        if len(part) > 2 and part.casefold() not in NAME_PARTICLES
    ]
    whole = " ".join(name.split())
    patterns = [re.compile(rf"\b{re.escape(whole)}\b", re.I)]
    patterns += [
        re.compile(rf"\b{re.escape(part)}\b{BEFORE_A_YEAR}", re.I)
        for part in dict.fromkeys(parts)
        if part.casefold() != whole.casefold()
    ]
    return patterns


def _tidy(text: str) -> str:
    """A removed line leaves an empty one behind; collapse the gaps it makes."""
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip() != "[date of birth removed]"]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
