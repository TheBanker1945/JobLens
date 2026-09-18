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
        description="The hiring company. For agency vacancies: the agency."
    )
    city: str | None = Field(description="City where the job is based.")
    work_mode: WorkMode | None = Field(
        description="onsite, hybrid (partly from home) or remote (fully from home)."
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
        description="Salary info that is not a number, e.g. 'schaal 11 cao Gemeenten' "
        "or 'marktconform'."
    )
    education_level: EducationLevel | None = Field(
        description="Minimum REQUIRED education level. None if optional or not stated."
    )
    experience_years_min: int | None = Field(
        ge=0, description="Minimum years of work experience asked for."
    )
    skills: list[str] = Field(
        description="Concrete hard skills and tools asked for, e.g. SQL, Python, "
        "Power BI. Not soft skills."
    )
    languages_required: list[str] = Field(
        description="Languages the candidate MUST speak, in English, e.g. Dutch. "
        "Not languages that are only a plus."
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
        return self


def _both(a, b) -> bool:
    return a is not None and b is not None
