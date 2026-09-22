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
SUBSTITUTIONS = str.maketrans(
    {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}
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
    """
    cleaned = searchable(quote).strip("\"'. ")
    if not cleaned:
        return False
    pattern = rf"(?<![{INSIDE_A_WORD}]){re.escape(cleaned)}(?![{INSIDE_A_WORD}])"
    return re.search(pattern, source) is not None
