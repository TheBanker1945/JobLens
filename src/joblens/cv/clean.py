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

from joblens.sources.clean import EMAIL, PHONE

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
POSTCODE = re.compile(
    r"\b[1-9]\d{3} ?(?!(?:" + "|".join(NOT_A_POSTCODE) + r")\b)[A-Za-z]{2}\b(?!-)"
)

# A street line: a word ending in a Dutch street suffix, then a house number.
# Matching the suffix rather than a capital letter keeps "Java 8, Spring Boot 3"
# out of it.
STREET = re.compile(
    r"\b[A-Za-zÀ-ſ'.-]*"
    r"(?:straat|laan|weg|plein|kade|dijk|singel|gracht|hof|pad|dreef|steeg|baan|"
    r"boulevard|park)"
    r"\s+\d+\s*[a-zA-Z]?\b",
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

# A burgerservicenummer is nine digits, and an IBAN is unmistakable. Neither
# belongs on a CV, and both are the worst thing to leak, so they are removed
# even though they are rare.
BSN = re.compile(r"(?<![\w-])\d{9}(?![\w-])")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z]{4}\d{10}\b")

# Order matters: a URL is removed before the e-mail inside it can be, and the
# birth *line* before the date on it, so the removal is reported as one thing.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url", URL),
    ("url", BARE_PROFILE),
    ("email", EMAIL),
    ("iban", IBAN),
    ("date of birth", BIRTH_LINE),
    ("date of birth", FULL_DATE),
    ("phone", PHONE),
    ("id number", BSN),
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


def _name_patterns(name: str) -> list[re.Pattern[str]]:
    """The whole name first, then each part, so "Jan de Vries" does not survive
    as "Vries" further down the page. Parts of one or two letters (initials, and
    "de", "van") are left alone: they match too much."""
    parts = [part for part in name.split() if len(part) > 2]
    whole = " ".join(name.split())
    return [
        re.compile(rf"\b{re.escape(piece)}\b", re.I)
        for piece in dict.fromkeys([whole, *parts])
    ]


def _tidy(text: str) -> str:
    """A removed line leaves an empty one behind; collapse the gaps it makes."""
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip() != "[date of birth removed]"]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
