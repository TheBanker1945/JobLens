"""What the judge is told about a person's own rules, and nothing more.

Most preferences are applied in code (rerank.py), because the facts they need
-- a contract type, a salary, a place -- are fields extraction already filled.
Three need reading instead, and go to the judge in a block after the CV:

- **Years past the CV.** Mahdi's labels in 3.7.2 disagreed with the judge on
  three vacancies he would apply to, each called weak for asking more years
  than his CV shows or an hbo/wo degree. Prompt 3.7 tried being lenient on
  years and degrees for everybody and did not pass its rule (6.2). This is not
  that: nothing changes unless the person says so, and then only as far as
  they said.
- **A degree level the CV does not show.** The same, for mbo/hbo/wo.
- **Sectors to avoid.** No extracted field names a sector, and "defence" or
  "gambling" is something a model can recognise in an advert and quote. The
  quote is checked like every other (cv/judge.py `verify`).

**Only when answered.** A person who answered none of the three gets no block,
so their judge prompt is prompt 3.6 to the byte, and their runs stay
comparable with every run before 7.3.

**Their words are data, not instructions.** The block quotes the answers
inside a fixed frame; the sectors are the only free text, one short line each.
"""

from joblens.preferences.schema import ANY_YEARS, Preferences

HEADING = "## What this person has told us"

FRAME = """\
These are their own answers about where they would apply. They are not facts
about their past: never use them as evidence, and never quote them. Where one
contradicts a general rule you were given, their answer wins."""

# One short line per sector, so a pasted paragraph cannot become a prompt.
LONGEST_SECTOR = 60


def for_judge(preferences: Preferences | None) -> str | None:
    """The block to show the judge, or None when there is nothing to tell it."""
    if preferences is None:
        return None
    lines = []
    years = preferences.stretch_years
    if years is not None:
        if years == 0:
            lines.append(
                "- They do not apply when a vacancy asks more years of experience "
                "than their CV shows: such a requirement makes the verdict weak."
            )
        else:
            how_many = (
                "however many years it asks"
                if years >= ANY_YEARS
                else f"up to {years} more year{'s' if years > 1 else ''} of "
                "experience than their CV shows"
            )
            lines.append(
                f"- They apply when a vacancy asks {how_many}. Such a requirement "
                "is still a gap (required), but on its own it does not make the "
                "verdict weak."
            )
    if preferences.stretch_degree is True:
        lines.append(
            "- They apply when a vacancy asks a degree level (mbo, hbo, wo) their "
            "CV does not show. Still name it as a gap (required); on its own it "
            "does not make the verdict weak."
        )
    elif preferences.stretch_degree is False:
        lines.append(
            "- They do not apply when a vacancy asks a degree level (mbo, hbo, "
            "wo) their CV does not show: such a requirement makes the verdict weak."
        )
    sectors = [
        " ".join(one.split())[:LONGEST_SECTOR] for one in preferences.avoid_sectors
    ]
    if sectors:
        lines.append(
            f"- They do not want to work in: {'; '.join(sectors)}. If this vacancy "
            "is in one of these, the verdict is weak: name it as a gap, quoting "
            "the vacancy."
        )
    if not lines:
        return None
    return "\n\n".join([HEADING, FRAME, "\n".join(lines)])
