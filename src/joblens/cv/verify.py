"""Is this string really in that text? The one comparison the app trusts.

Milestone 3.6 put this inside the judge: every quote it produces is looked for in
the CV or the vacancy it claims to come from, and a claim whose quote is not
there is deleted before anyone reads it. Milestone 3.6.1 needs the same guarantee
one step earlier -- a *year* in an extracted profile has to be in the CV or it is
the model's invention -- so the comparison lives here and both callers use it.

The rule it encodes, and the reason it is worth its own module:

    **Normalising formatting is safe, accepting a paraphrase is not.**

A character that carries no meaning cannot make two different claims look like
the same one, so case, whitespace and markdown emphasis are removed from both
sides. A loosened *word* match could, so there is none: no stemming, no fuzzy
distance, no "close enough". Everything this module removes is listed below, and
the list is the whole argument.
"""

import re

# Characters a model swaps without meaning to: a CV writes ' and the answer comes
# back with the typographic version, or an em dash arrives as a hyphen.
#
# And the typographic ligatures a PDF can carry: a CV typeset in LaTeX says
# "ﬁnding" where the model, and everyone reading it, says "finding".
SUBSTITUTIONS = str.maketrans(
    {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}
    | {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
)

# Formatting, not words. The first version of this check threw away a perfectly
# real quote from Sanne's CV, because the source line is
#
#     **MBO Verpleegkunde niveau 4 — ROC Midden Nederland, Utrecht**
#
# and the model quoted it without the asterisks -- which is the right thing to
# do, and failed a literal comparison.
MARKUP = str.maketrans(dict.fromkeys("*_`>|•·", None))

# "#" is markup at the start of a word (a markdown heading, a hashtag) and part
# of the word after a letter: "C#" and "F#" are languages, and removing the "#"
# made a quote of "C#" match a CV that says "C++" (2026-09-22 audit).
HEADING_HASH = re.compile(r"(?<!\w)#+")

# Where a quote may start and end in the source: not inside a word. "+" and "#"
# count as part of a word here, so "C" does not stand in for "C++" or "C#".
INSIDE_A_WORD = r"\w+#"

# A hyphen at the end of a line, once `searchable` has turned the line break
# into a space: "Cool- blue". Between two letters only, so "2019 - 2021" and
# "Engineer - Amsterdam" are not line breaks.
LINE_END_HYPHEN = re.compile(r"(?<=[^\W\d_])- (?=[^\W\d_])")

# How a hyphen at a line end may be read: as the text has it ("consul- tancy",
# which is what a quote that stops at the break matches), as the typesetter
# splitting a word ("Cool-⏎blue" is Coolblue), or as a real hyphen that
# happened to fall there ("One-⏎Class" is One-Class). The page does not say
# which of the last two it is; the first keeps every quote that passed before
# this rule existed passing.
LINE_END_READINGS = (None, "", "-")


def searchable(text: str) -> str:
    """Case, whitespace and formatting removed; every word kept.

    Whitespace is collapsed so that a quote crossing a line break in the original
    still matches the one the model wrote on a single line.
    """
    plain = HEADING_HASH.sub("", text.translate(SUBSTITUTIONS)).translate(MARKUP)
    return " ".join(plain.casefold().split())


def quoted(quote: str, source: str) -> bool:
    """Whether `quote` appears in `source` as whole words.

    `source` must already be `searchable`. Whole words, because a substring
    test verified "Java" against a CV that only says "JavaScript", "Excel"
    against "Excellent" and "Go" against "Google" -- a claim the CV does not
    make, passed by the one check that exists to stop that. Requiring the
    quote to start and end on a word boundary is a tightening: nothing that
    failed before can pass now.

    A word broken over two lines is read both ways it can be read, on both
    sides, and the quote has to match one reading exactly. On ten real CVs
    (2026-09-24) seven of eleven dropped claims were true and quoted the way a
    person reads "Cool-⏎blue" or "Univer-⏎sity"; LaTeX hyphenates, and every
    CV written in it is one long line-break test. This is where a line ended,
    not what the words are: a hyphen typed inside a line is left alone, so
    "Javaontwikkelaar" is still not found in "Java-ontwikkelaar".
    """
    cleaned = searchable(quote).strip("\"'. ")
    if not cleaned:
        return False
    return any(
        _found(_read(cleaned, joined), _read(source, joined))
        for joined in LINE_END_READINGS
    )


def _read(text: str, joined: str | None) -> str:
    return text if joined is None else LINE_END_HYPHEN.sub(joined, text)


def _found(quote: str, source: str) -> bool:
    pattern = rf"(?<![{INSIDE_A_WORD}]){re.escape(quote)}(?![{INSIDE_A_WORD}])"
    return re.search(pattern, source) is not None
