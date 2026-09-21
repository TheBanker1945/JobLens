"""Shared test helpers.

`VacancyDetails` has no defaults on purpose -- the model must answer every
field, so "not in the text" is always a deliberate null rather than a field the
model forgot. That makes one useful in a test verbose to write, so it is built
here once.
"""

from joblens.extraction.schema import VacancyDetails

NOT_STATED = dict.fromkeys(
    (
        "company",
        "city",
        "work_mode",
        "hours_min",
        "hours_max",
        "salary_min",
        "salary_max",
        "salary_period",
        "salary_note",
        "education_level",
        "experience_years_min",
        "contract_type",
    )
)


def details(title: str, **stated) -> VacancyDetails:
    """Details with `title` and whatever else is given; the rest is null."""
    return VacancyDetails(
        title=title, skills=[], languages_required=[], **NOT_STATED | stated
    )
