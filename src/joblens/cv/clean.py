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
NOT_A_YEAR_AND_ACRONYM = (
    r"(?!(?:19|20)\d{2} ?(?:" + "|".join(ACRONYMS_AFTER_A_YEAR) + r")\b)"
)
POSTCODE = re.compile(
    r"\b" + NOT_A_YEAR_AND_ACRONYM + r"[1-9]\d{3} ?"
    r"(?!(?:" + "|".join(NOT_A_POSTCODE) + r")\b)[A-Za-z]{2}\b(?!-)"
)

# The line above a postcode, or the words before it on the same line, when they
# end in a house number: "De Drie Linden 14" over "2345 XY Voorschoten". STREET
# below needs a Dutch street suffix, and two of ten real CVs (2026-09-24) lived
# on a street without one -- "Molenakkers 5B" -- so the postcode went and the
# street stayed. The postcode is what makes this safe: it has to be the address
# kind, capitals and then a city or the end of the line, so "2021 AB testing"
# under a line ending in a version number is not an address.
ADDRESS_BEFORE_POSTCODE = re.compile(
    r"(?m)^[ \t]*[^\W\d_][^\d\n]{0,40}?[ \t]+(?!(?:19|20)\d{2}\b)\d{1,5}[ \t]*"
    r"[a-zA-Z]?\b"
    r"(?=[ \t,]*(?:\n[ \t]*)?" + NOT_A_YEAR_AND_ACRONYM + r"[1-9]\d{3} ?[A-Z]{2}\b"
    r"[ \t,]*(?:[A-Z]|$))"
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

# "Geboortedatum: 3 maart 1995" -- the label and the date after it. Not the whole
# line: a real CV wrote "Geboren 3 maart 1995 – woonachtig in Woerden", and
# taking the line took the one fact on it that matching needs (2026-09-24). The
# "[" guard stops the rule from finding its own "[date of birth removed]".
BIRTH_LABEL = (
    r"(?<!\[)\b(?:geboortedatum|geboortedag|geboren(?:\s+op)?|date\s+of\s+birth|"
    r"birth\s*date|dob)\b"
)
BIRTH_DATE = re.compile(
    rf"(?i){BIRTH_LABEL}[^\n]{{0,30}}?\b(?:19|20)\d{{2}}\b(?:[-/.]\d{{1,2}}){{0,2}}"
)
# A label with no four-digit year after it ("Geboren: 3-5-'95") still takes the
# rest of the line, because there is no end of the date to stop at.
BIRTH_LINE = re.compile(rf"(?im){BIRTH_LABEL}.*$")
# A full day-month-year date. Work history is written in years or months
# ("2019 - 2023", "maart 2021"), so a complete date in a CV is almost always a
# date of birth -- and on the rare occasion it is a project date, losing it
# costs nothing for matching.
FULL_DATE = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}\b")

URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
# A profile link written without a scheme, e.g. "linkedin.com/in/jane-doe".
#
# No "\b" in front, except for x.com, which would otherwise be found at the end
# of any domain. A CV whose contact line is set in an icon font extracts the
# icon as letters glued to the link -- "nednlinkedin.com/in/...",
# "gtbgithub.com/..." on a real CV (2026-09-24) -- and a word boundary there is
# exactly what does not exist.
PROFILE_HOSTS = (
    "linkedin.com github.com gitlab.com bitbucket.org codeberg.org twitter.com "
    "instagram.com facebook.com stackoverflow.com medium.com youtube.com "
    "kaggle.com huggingface.co behance.net dribbble.com"
).split()
BARE_PROFILE = re.compile(
    r"(?:\bx\.com|" + "|".join(re.escape(host) for host in PROFILE_HOSTS) + r")/\S+",
    re.I,
)
# "Github:// jbakker", "Maven:// nl.jbakker": a site name and a handle, written
# as if it were a link. Only this shape: "GitHub: Actions" in a skills list is a
# skill, and "://" is never how a skill is written.
LABELLED_HANDLE = re.compile(r"\b[A-Za-z]+:// ?@?[\w.-]+")

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

# A number *with* its "+", glued to the letters in front of it. `PHONE` wants
# no letter before a number, which is right in prose and wrong on a contact line
# set in an icon font: the phone icon extracts as letters, "ne+31 6 12 345 678"
# (a real CV, 2026-09-24, on both of its language versions), and the number
# went to the cloud. A "+" and seven or more digits is a phone number whatever
# stands in front of it.
GLUED_PHONE = re.compile(
    r"(?<=[^\W\d_])\+\d{1,3}"
    + SEPARATOR
    + r"(?:\(0\)"
    + SEPARATOR
    + r")?\d(?:"
    + SEPARATOR
    + r"\d){6,11}(?![\w-])"
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
    ("url", LABELLED_HANDLE),
    ("email", EMAIL),
    ("iban", IBAN),
    ("date of birth", BIRTH_DATE),
    ("date of birth", BIRTH_LINE),
    ("date of birth", FULL_DATE),
    # The country-code rule goes first: "0031 70 700 0510" starts with a zero,
    # so PHONE matches it as a national number and stops at the first place it
    # can, leaving the last block on the page.
    ("phone", COUNTRY_CODE_PHONE),
    ("phone", PHONE),
    ("phone", GLUED_PHONE),
    ("long number", DIGIT_RUN),
    # Before STREET, so "Jan Steenstraat 1" goes as one address and not as a
    # street with a first name left in front of it.
    ("address", ADDRESS_BEFORE_POSTCODE),
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
        for kind, pattern in _name_patterns(name):
            text = _remove(text, pattern, kind, removals)
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


# A name part at least this long is looked for *inside* a domain name, where
# "bakkerjb.com" or "janbakker.nl" is a personal site. Shorter parts are
# not: a three-letter surname is too likely to be the start of an employer's
# domain.
MIN_PART_IN_A_DOMAIN = 4

# Characters an identifier is made of: a handle, a domain, the path of a link.
IDENTIFIER = r"[\w.@/-]"


def _name_patterns(name: str) -> list[tuple[str, re.Pattern[str]]]:
    """The whole name first, then each part, so "Jan de Vries" does not survive
    as "Vries" further down the page. Initials, two-letter words and the
    particles above are left alone: they match too much of an ordinary CV.

    Before either, the two places a name hides from a word match (found on four
    of ten real CVs, 2026-09-24): run together into a handle
    ("LinkedIn:// janbakker"), and inside a personal domain
    ("janbakker.nl", "bakkerjb.com"). The whole handle or domain goes,
    since what is left of it would still point at the person.
    """
    words = name.split()
    parts = [
        part
        for part in words
        if len(part) > 2 and part.casefold() not in NAME_PARTICLES
    ]
    whole = " ".join(words)
    # A run needs a real part in it: "van der" alone would join to "vander" and
    # take Vanderlande with it.
    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("name", re.compile(_joined(run), re.I))
        for run in _runs(words)
        if len("".join(run)) >= 6 and any(word in parts for word in run)
    ]
    patterns += [
        (
            "url",
            re.compile(
                rf"(?<![\w.@-])[\w-]*{re.escape(part)}[\w-]*(?:\.[\w-]+)*"
                rf"\.[a-z]{{2,6}}\b(?:/\S*)?",
                re.I,
            ),
        )
        for part in dict.fromkeys(parts)
        if len(part) >= MIN_PART_IN_A_DOMAIN
    ]
    patterns.append(("name", re.compile(rf"\b{re.escape(whole)}\b", re.I)))
    patterns += [
        ("name", re.compile(rf"\b{re.escape(part)}\b{BEFORE_A_YEAR}", re.I))
        for part in dict.fromkeys(parts)
        if part.casefold() != whole.casefold()
    ]
    return patterns


def _runs(words: list[str]) -> list[list[str]]:
    """Every stretch of two or more consecutive words of the name, longest first:
    "Jan van der Berg" gives "janvanderberg", "vanderberg", "derberg" and so on."""
    return [
        words[start:end]
        for length in range(len(words), 1, -1)
        for start in range(len(words) - length + 1)
        for end in [start + length]
    ]


def _joined(run: list[str]) -> str:
    """The words written as one handle -- "janbakker", "jan-bakker",
    "jan.bakker" -- and the rest of the identifier around them."""
    handle = r"[-_.]?".join(re.escape(word) for word in run)
    return rf"{IDENTIFIER}*{handle}{IDENTIFIER}*"


def _tidy(text: str) -> str:
    """A removed line leaves an empty one behind; collapse the gaps it makes."""
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip() != "[date of birth removed]"]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
