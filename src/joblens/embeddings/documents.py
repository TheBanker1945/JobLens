"""What text do we embed for a vacancy?

An embedding compresses a whole text into one vector, so long texts dilute: in
milestone 2.1 the query "python baan in amsterdam" ranked a product manager above
the only Python job in Amsterdam, because "Python" was one word in a long story.

Three styles, compared in the 2.2 retrieval eval:
- raw:        the vacancy text as published (the 2.1 baseline)
- structured: a short summary built from the extracted fields
- title_only: only the job title (a deliberately minimal baseline)
"""

from typing import Literal

from joblens.extraction.schema import VacancyDetails

Style = Literal["raw", "structured", "title_only"]
STYLES: tuple[Style, ...] = ("raw", "structured", "title_only")


def build_document(
    text: str, details: VacancyDetails | None = None, style: Style = "raw"
) -> str:
    if style == "raw":
        return text
    if details is None:
        raise ValueError(f"style {style!r} needs extracted details")
    if style == "title_only":
        return details.title
    if style == "structured":
        return _structured(details)
    raise ValueError(f"unknown style {style!r}")


def _structured(d: VacancyDetails) -> str:
    """Dutch labels: the vacancies are Dutch, so a Dutch summary stays closest to
    the language a Dutch job seeker searches in."""
    hours = _range(d.hours_min, d.hours_max)
    salary = _range(d.salary_min, d.salary_max)
    lines = [
        d.title,
        _line("Bedrijf", d.company),
        _line("Plaats", d.city),
        _line("Werkvorm", d.work_mode),
        _line("Uren per week", hours),
        _line("Dienstverband", d.contract_type),
        _line("Opleidingsniveau", d.education_level),
        _line(
            "Werkervaring",
            f"{d.experience_years_min} jaar" if d.experience_years_min else None,
        ),
        _line(
            "Salaris", f"{salary} per {d.salary_period}" if salary else d.salary_note
        ),
        _line("Vaardigheden", ", ".join(d.skills)),
        _line("Talen", ", ".join(d.languages_required)),
    ]
    return "\n".join(line for line in lines if line)


def _line(label: str, value) -> str | None:
    return f"{label}: {value}" if value else None


def _range(low, high) -> str | None:
    if low is None and high is None:
        return None
    if low is None:
        return f"tot {high:g}"
    if high is None or low == high:
        return f"{low:g}"
    return f"{low:g}-{high:g}"
