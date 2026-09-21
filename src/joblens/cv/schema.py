"""What we read out of a CV.

Deliberately shaped like `VacancyDetails`: the same skills, the same languages
as English names, the same education vocabulary. A CV and a vacancy have to be
compared field by field in the gap report, and two vocabularies would mean a
translation step that can be wrong.

As in extraction, the field descriptions are instructions: they end up in the
JSON schema the model receives, and `None` always means "the CV does not say",
never "probably not".

Two things are computed here rather than asked of the model: how many years of
experience a CV shows, and whether it meets an education requirement. A model
that is asked to add up years will produce a plausible number; a model that is
asked to copy dates produces dates, and the arithmetic is then ours and
checkable.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from joblens.extraction.schema import EducationLevel, WorkMode


# A CV states levels a vacancy never asks for -- vwo, a doctorate -- so the
# vacancy enum is too small to describe one, and widening the vacancy enum would
# change what is already stored for 198 vacancies. Hence a second, larger
# vocabulary that contains the first.
class CVEducationLevel(StrEnum):
    VMBO = "vmbo"
    MBO = "mbo"
    HAVO = "havo"
    VWO = "vwo"
    HBO = "hbo"
    WO = "wo"
    PHD = "phd"


# A rough ladder for one question only: does this CV clear the level a vacancy
# asks for? Equal ranks mean "close enough to each other to not decide it"
# (mbo-4 and havo both open the door to hbo). It is not a statement about Dutch
# education policy, and nothing but `meets_level` should read it.
LEVEL_RANK: dict[str, int] = {
    CVEducationLevel.VMBO: 1,
    CVEducationLevel.MBO: 2,
    CVEducationLevel.HAVO: 2,
    CVEducationLevel.VWO: 3,
    CVEducationLevel.HBO: 4,
    CVEducationLevel.WO: 5,
    CVEducationLevel.PHD: 6,
}


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")

    programme: str = Field(
        description="The study programme as the CV writes it, e.g. 'Bedrijfskunde' "
        "or 'Elektrotechniek'. For a course or training without a programme name, "
        "the name of the course."
    )
    level: CVEducationLevel | None = Field(
        description="The Dutch level of this education, using the exact values in "
        "the schema. A bachelor at a hogeschool is hbo, a university bachelor or "
        "master is wo, a doctorate is phd. null if the CV does not make the level "
        "clear -- never guess it from the programme name."
    )
    institution: str | None = Field(description="School, college or university.")
    end_year: int | None = Field(
        ge=1950,
        le=2100,
        description="Year it ended, or the expected year if the CV says so. null "
        "if no year is given.",
    )
    finished: bool | None = Field(
        description="true if the CV says a diploma or degree was obtained, false if "
        "it says the study was not completed ('niet afgerond', 'gestopt'), null if "
        "it says neither."
    )


class Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="The job title as the CV writes it.")
    company: str | None = Field(description="Employer or client.")
    start: str | None = Field(
        pattern=r"^\d{4}(-(0[1-9]|1[0-2]))?$",
        description="When it started, as 'YYYY-MM', or 'YYYY' when the CV gives "
        "only a year. null if no start is given.",
    )
    end: str | None = Field(
        pattern=r"^\d{4}(-(0[1-9]|1[0-2]))?$",
        description="When it ended, same format. null if the job is still ongoing "
        "or the CV gives no end.",
    )
    current: bool = Field(
        description="true if the CV presents this as the current job ('heden', "
        "'present', 'now')."
    )
    summary: str | None = Field(
        description="What this person did in this job, in the CV's own words, at "
        "most two sentences. null if the CV lists only the title and dates."
    )
    skills: list[str] = Field(
        description="Concrete skills, tools and technologies named for THIS job. "
        "One item per skill, keeping the CV's wording. Not soft skills."
    )


class CVProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(
        description="The role this CV presents itself as, in the CV's own words, "
        "e.g. 'Junior data-analist'. If the CV has no such line, the most recent "
        "job title."
    )
    summary: str | None = Field(
        description="The CV's own profile or 'over mij' paragraph, shortened to at "
        "most three sentences. null if the CV has none: never write one."
    )
    city: str | None = Field(
        description="The city or town this person lives in, if the CV names one. "
        "Not the street. Not the city of an employer."
    )
    experience: list[Experience] = Field(
        description="Every paid job, in the order the CV lists them. Internships "
        "and side jobs count."
    )
    education: list[Education] = Field(
        description="Every study, course and training the CV lists."
    )
    skills: list[str] = Field(
        description="Every concrete skill, tool, technology or knowledge area in "
        "the whole CV, including those already named under a job. One item per "
        "skill, keeping the CV's wording. Not soft skills."
    )
    certificates: list[str] = Field(
        description="Certificates, licences and diplomas that are not a study "
        "programme: 'VCA', 'rijbewijs B', 'heftruckcertificaat', 'AWS Certified'."
    )
    languages: list[str] = Field(
        description="Languages this person speaks, always as English language "
        "names: 'Nederlands' -> 'Dutch', 'Engels' -> 'English'."
    )
    desired_work_mode: WorkMode | None = Field(
        description="Only if the CV states a wish about working from home or on "
        "site. null otherwise."
    )
    availability: str | None = Field(
        description="What the CV says about hours, contract or start date, e.g. "
        "'32-40 uur, per direct'. null if it says nothing."
    )

    def all_skills(self) -> list[str]:
        """Skills from the whole CV and from each job, in CV order, deduplicated.

        Kept as the CV wrote them: "Power BI" and "power bi" are one skill, and
        the first spelling wins, because it is the one a person will recognise
        when the match report quotes it back at them.
        """
        seen: dict[str, str] = {}
        for skill in [
            *self.skills,
            *(s for job in self.experience for s in job.skills),
        ]:
            seen.setdefault(skill.casefold(), skill)
        return list(seen.values())

    def highest_level(self) -> CVEducationLevel | None:
        levels = [e.level for e in self.education if e.level]
        return max(levels, key=lambda level: LEVEL_RANK[level]) if levels else None

    def years_of_experience(self, today: date | None = None) -> float | None:
        """Calendar years covered by the jobs the CV lists, overlaps counted once.

        Not full-time-equivalent experience, and the difference is not small: a
        CV that lists a supermarket side job through three years of school gets
        those three years counted, because the CV does not say how many hours a
        week any of its jobs were. Youssef Bakker's sample CV says "ruim acht
        jaar" and this returns 13.7 for that reason.

        So it answers "how long has this person been working", which is a fact
        about the dates, and not "how experienced are they", which is a judgement
        and belongs to the matching step that can read the whole CV.

        Overlap: two jobs held at the same time are one stretch, not two, so
        the periods are merged before they are added up. A date given as a year
        only is read as that whole year, which rounds upwards -- a job listed as
        "2023" counts as twelve months, and both end months count in full.
        None when no job carries a start date,
        because a CV that does not say cannot be summarised into a number.
        """
        periods = [p for p in (_period(job, today) for job in self.experience) if p]
        if not periods:
            return None
        months, end_so_far = 0, -1
        for start, end in sorted(periods):
            start = max(start, end_so_far)  # the overlap is already counted
            if end > start:
                months += end - start
                end_so_far = end
        return round(months / 12, 1)


def meets_level(required: EducationLevel | None, profile: CVProfile) -> bool:
    """Whether this CV clears the education level a vacancy asks for.

    A vacancy that asks for nothing is met by anything, including a CV with no
    education section at all -- "not stated" is not a failure to meet a
    requirement that does not exist.
    """
    if required is None:
        return True
    held = profile.highest_level()
    return held is not None and LEVEL_RANK[held] >= LEVEL_RANK[required.value]


def _period(job: Experience, today: date | None) -> tuple[int, int] | None:
    """The job as (first month, month after the last), counted in months."""
    if job.start is None:
        return None
    today = today or date.today()
    now = today.year * 12 + today.month
    start = _months(job.start, first=True)
    if job.end:
        end = _months(job.end, first=False)
    elif job.current:
        end = now
    else:
        return None  # no end and not marked current: the CV does not say
    return (start, min(end, now))


def _months(value: str, *, first: bool) -> int:
    """'2021-03' -> that month; '2021' -> January or the December after it."""
    year, _, month = value.partition("-")
    if month:
        index = int(year) * 12 + int(month)
        return index if first else index + 1  # end is exclusive
    return int(year) * 12 + (1 if first else 13)
