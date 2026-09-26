"""Which language a page is shown in (7.7).

Mahdi (2026-09-25): "check their country of origin and make that the language
that they will see, and also have the option to switch languages. English,
dutch, german, french and spanish." What a server can see without asking anyone
is the browser's own list of languages (the Accept-Language header,
"nl-BE,nl;q=0.9,en;q=0.8"), each with an optional country. So, in order:

1. the language the person chose with the switcher (kept on their account);
2. the first language in the browser's list that JobLens has texts for --
   someone who set their browser to English reads English, wherever they are;
3. otherwise the country in that list: a browser set to Polish in the
   Netherlands ("pl-NL") gets Dutch, one set to Turkish in Germany German;
4. otherwise English.

Only languages whose texts exist are offered (ui/assets/i18n/<code>.json):
Dutch and English in step 1, German, French and Spanish in step 4. A browser
that asks for German before then gets English, not a page of missing keys.
"""

from pathlib import Path

SUPPORTED = ("en", "nl", "de", "fr", "es")
DICTIONARIES = Path(__file__).parent / "ui" / "assets" / "i18n"

# A country, as the second part of a language tag, and the language JobLens
# shows there. Belgium is Dutch-first (Flanders is the larger half), but a
# browser set to French in Belgium asks for French and gets it under rule 2.
COUNTRY_LANGUAGE = {
    "nl": "nl", "be": "nl", "sr": "nl", "aw": "nl", "cw": "nl",
    "de": "de", "at": "de", "ch": "de", "li": "de",
    "fr": "fr", "lu": "fr", "mc": "fr",
    "es": "es", "mx": "es", "ar": "es", "co": "es", "cl": "es", "pe": "es",
}  # fmt: skip


def available() -> tuple[str, ...]:
    """The languages whose texts are shipped, in SUPPORTED's order."""
    return tuple(
        code for code in SUPPORTED if (DICTIONARIES / f"{code}.json").is_file()
    )


def pick(
    accept_language: str | None,
    saved: str | None = None,
    offered: tuple[str, ...] | None = None,
) -> str:
    offered = available() if offered is None else offered
    if saved in offered:
        return saved
    tags = _ranked(accept_language or "")
    for primary, _country in tags:
        if primary in offered:
            return primary
    for _primary, country in tags:
        if COUNTRY_LANGUAGE.get(country) in offered:
            return COUNTRY_LANGUAGE[country]
    return "en"


def _ranked(header: str) -> list[tuple[str, str]]:
    """'nl-BE,en;q=0.8' -> [('nl', 'be'), ('en', '')], most wanted first.

    A malformed part is skipped rather than trusted; the header comes from
    whoever sent the request.
    """
    found: list[tuple[float, int, str, str]] = []
    for position, part in enumerate(header.split(",")[:20]):
        tag, _, params = part.strip().partition(";")
        pieces = tag.strip().lower().replace("_", "-").split("-")
        if not pieces[0].isalpha() or not 2 <= len(pieces[0]) <= 3:
            continue
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                continue
        country = next((p for p in pieces[1:] if len(p) == 2 and p.isalpha()), "")
        found.append((-quality, position, pieces[0], country))
    return [(primary, country) for _, _, primary, country in sorted(found)]
