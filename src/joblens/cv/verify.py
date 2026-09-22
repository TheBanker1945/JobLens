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
MARKUP = str.maketrans(dict.fromkeys("*_`#>|•·", None))


def searchable(text: str) -> str:
    """Case, whitespace and formatting removed; every word kept.

    Whitespace is collapsed so that a quote crossing a line break in the original
    still matches the one the model wrote on a single line.
    """
    plain = text.translate(SUBSTITUTIONS).translate(MARKUP)
    return " ".join(plain.casefold().split())


def quoted(quote: str, source: str) -> bool:
    """Whether `quote` appears in `source`, which must already be `searchable`."""
    cleaned = searchable(quote).strip("\"'. ")
    return bool(cleaned) and cleaned in source
