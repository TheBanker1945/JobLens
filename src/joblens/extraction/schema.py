"""What we extract from a vacancy text.

The field descriptions are not just documentation: they end up in the JSON schema the
model receives, so they work as per-field instructions. `None` means "not in the text";
the model must never guess.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkMode(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"


class SalaryPeriod(StrEnum):
    HOUR = "hour"
    MONTH = "month"
    YEAR = "year"


class EducationLevel(StrEnum):
    MBO = "mbo"
    HBO = "hbo"
    WO = "wo"


class ContractType(StrEnum):
    PERMANENT = "permanent"
    TEMPORARY = "temporary"
    TEMP_AGENCY = "temp_agency"
    FREELANCE = "freelance"
    INTERNSHIP = "internship"


class VacancyDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no invented extra fields

    title: str = Field(
        description="The job title only, without company, city, hours or 'Vacature:'."
    )
    company: str | None = Field(
        description="The hiring company's name. For agency vacancies: the agency. "
        "Never a section heading such as 'Over ons' or 'Over <naam>': use the name."
    )
    city: str | None = Field(description="City where the job is based.")
    work_mode: WorkMode | None = Field(
        description="hybrid: any option to work from home, even 'af en toe', or "
        "'plaats- en tijdonafhankelijk' with an office available. remote: fully from "
        "home. onsite: at the workplace, also when the work obviously cannot be done "
        "from home (e.g. a warehouse)."
    )
    hours_min: int | None = Field(
        ge=1, le=60, description="Minimum hours per week. Fulltime without hours: 40."
    )
    hours_max: int | None = Field(
        ge=1, le=60, description="Maximum hours per week. Same as hours_min if fixed."
    )
    salary_min: float | None = Field(
        gt=0,  # 0 is never a real salary; use null when no amount is given
        description="Lowest gross salary as a plain number, e.g. 3200.0.",
    )
    salary_max: float | None = Field(
        gt=0, description="Highest gross salary. Same as salary_min if one amount."
    )
    salary_period: SalaryPeriod | None = Field(
        description="Period the salary amounts are per. Required if a salary is given."
    )
    salary_note: str | None = Field(
        description="Salary information beyond the amounts, e.g. 'afhankelijk van "
        "ervaring', a shift bonus or holiday allowance. When there are no amounts: the "
        "salary text itself, e.g. 'schaal 11 cao Gemeenten' or 'marktconform'. "
        "null only if the text says nothing more about pay."
    )
    education_level: EducationLevel | None = Field(
        description="Minimum REQUIRED education level. 'Hbo-denkniveau' counts as hbo. "
        "null if a level is only nice to have ('mooi, maar niet verplicht') or not "
        "stated."
    )
    experience_years_min: int | None = Field(
        ge=0,
        description="Minimum years of work experience, only if stated as a number. "
        "null for vague terms like 'enkele jaren' or 'ruime ervaring'.",
    )
    skills: list[str] = Field(
        description="Every concrete skill, tool, technology, certificate or knowledge "
        "area in the whole text: requirements, nice-to-haves ('een pré', 'a plus') "
        "AND tools named in the task description. One item per skill: 'Python "
        "(Django of FastAPI)' -> 'Python', 'Django', 'FastAPI'. Keep the wording of "
        "the text. Not soft skills and not names of study programmes."
    )
    languages_required: list[str] = Field(
        description="Languages the candidate MUST speak, in English, e.g. Dutch. "
        "Not languages that are only a plus. If any one of several languages is "
        "enough ('Nederlands of Engels'), none is required on its own: []."
    )
    contract_type: ContractType | None = Field(
        description="permanent (vast), temporary (tijdelijk/jaarcontract), temp_agency "
        "(uitzend), freelance or internship (stage)."
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if _both(self.hours_min, self.hours_max) and self.hours_min > self.hours_max:
            raise ValueError("hours_min is greater than hours_max")
        if (
            _both(self.salary_min, self.salary_max)
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min is greater than salary_max")
        has_salary = self.salary_min is not None or self.salary_max is not None
        if has_salary and self.salary_period is None:
            raise ValueError("salary_period is required when a salary amount is given")
        if self.salary_period is not None and not has_salary:
            raise ValueError(
                "salary_period is set but no salary amount is given; use null"
            )
        return self


def _both(a, b) -> bool:
    return a is not None and b is not None
