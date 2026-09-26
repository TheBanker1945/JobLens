"""What a person wants from their next job: the questionnaire's answers (7.3).

Kept apart from the CV on purpose. A CV is facts about someone's past;
preferences are constraints on their future. Mixing them is why "4 years
required" kept misfiring: a CV cannot say "I apply anyway", a preference can.

**Empty means no preference, never no.** Every field can be left blank, and a
blank field moves nothing. The questions are the ones agreed on 2026-09-25
(docs/web-app-phase-7.md): contract, hours, work mode, where and how far,
salary, seniority, languages, employers and sectors to avoid, and whether to
apply when a vacancy asks more years or a higher degree than the CV shows.

**Stated, never inferred.** Every value here is something the person answered.
Nothing in JobLens writes to it from a pattern in their labels (CLAUDE.md).
"""

import hashlib
import json
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from joblens.extraction.schema import ContractType, WorkMode
from joblens.preferences.places import Geo

# How preferences act: the re-ranking rules (rerank.py) and the block the judge
# is shown (prompt.py). Stamped on every run that used preferences, together
# with a digest of the answers, so a run can say which rules moved it. Bump it
# when either changes.
PREFERENCES_VERSION = "p1"

# "However many years it asks." The questionnaire offers no, 1, 2, 3 or any.
ANY_YEARS = 10


class Seniority(StrEnum):
    JUNIOR = "junior"
    MEDIOR = "medior"
    SENIOR = "senior"
    LEAD = "lead"


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    contract_types: list[ContractType] = []  # empty: any contract
    hours_min: int | None = Field(None, ge=0, le=60)  # per week
    hours_max: int | None = Field(None, ge=0, le=60)
    work_modes: list[WorkMode] = []
    home: str | None = None  # a Dutch place, for the distance below
    max_distance_km: int | None = Field(None, ge=1, le=300)  # as the crow flies
    salary_min: int | None = Field(None, ge=0)  # euro gross per month
    seniority: list[Seniority] = []
    languages: list[str] = []  # the languages they work in, as extraction names them
    avoid_employers: list[str] = []
    avoid_sectors: list[str] = []  # read by the judge: no field says a sector
    # Up to how many years more than the CV shows they still apply for
    # (0: none, ANY_YEARS: any). None: not answered, and the judge is told
    # nothing.
    stretch_years: int | None = Field(None, ge=0, le=ANY_YEARS)
    # Whether they apply when a vacancy asks a degree level (mbo, hbo, wo) the
    # CV does not show. None: not answered.
    stretch_degree: bool | None = None

    @field_validator("languages")
    @classmethod
    def _as_extraction_names_them(cls, languages: list[str]) -> list[str]:
        """Extraction writes "Dutch", so "dutch" and "Dutch" are one language."""
        return [one.strip().capitalize() for one in languages if one.strip()]

    @field_validator("avoid_employers", "avoid_sectors")
    @classmethod
    def _no_blanks(cls, names: list[str]) -> list[str]:
        return [one.strip() for one in names if one.strip()]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (
            self.hours_min is not None
            and self.hours_max is not None
            and self.hours_min > self.hours_max
        ):
            raise ValueError("hours_min is more than hours_max")
        if self.max_distance_km is not None and not self.home:
            raise ValueError("a maximum distance needs a home to measure it from")
        if self.home and not Geo.load().knows(self.home):
            raise ValueError(
                f"{self.home!r} is not a Dutch place JobLens can find on the map; "
                "use the name of the town or city"
            )
        return self

    def is_empty(self) -> bool:
        return self == Preferences()

    def stamp(self) -> str:
        """What a run records: the rules' version and a digest of the answers.
        "" when nothing was answered, so such a run reads like one before 7.3."""
        if self.is_empty():
            return ""
        answers = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return (
            f"{PREFERENCES_VERSION}:{hashlib.sha256(answers.encode()).hexdigest()[:8]}"
        )


class Conflict(BaseModel):
    """One stated preference a vacancy contradicts, in its own words.

    Structured rather than a sentence, so the web page can say it in the
    person's language; `line()` is the English the CLI prints.
    """

    # contract, hours, work_mode, salary, distance, language, seniority, employer
    field: str
    found: str  # what the vacancy says
    wanted: str  # what the person said

    def line(self) -> str:
        return f"{self.field}: {self.found} -- you want {self.wanted}"
