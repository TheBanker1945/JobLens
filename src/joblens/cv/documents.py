"""What text do we search with, when the searcher is a CV?

The vacancy side of this question was settled in 3.1: a vacancy is indexed as a
`structured` summary of its extracted fields, because raw advert text embeds the
employer's boilerplate as much as the job. This is the same question from the
other end, and it has an extra twist: a vacancy is one job, but a CV is five jobs,
an education and a pile of skills. One vector for all of that is an average of
things that are not alike.

Five candidates, measured against each other in 3.5:

- raw:      the redacted CV text, as one query. The baseline.
- profile:  a summary of the extracted fields, in the same shape and the same
            Dutch labels as the vacancy documents it will be compared with.
- roles:    one query per job, plus one for education and skills. A vacancy then
            scores as the single part of the career it fits best.
- chunks:   the CV cut into overlapping pieces by `chunk_text`, which is the same
            idea as `roles` without understanding anything about CVs.
- wishlist: an invented advert for the job this person would plausibly be hired
            for next, written by a model and used as the query (see wishlist.py).

`roles` and `chunks` produce several queries, which `VacancyIndex.search_many`
pools by taking each vacancy's best part. Which part won is kept, so a result can
say what in the CV it answered.
"""

from dataclasses import dataclass
from typing import Literal

from joblens.cv.schema import CVProfile
from joblens.embeddings.documents import chunk_text

CVStyle = Literal["raw", "profile", "roles", "chunks", "wishlist"]
CV_STYLES: tuple[CVStyle, ...] = ("raw", "profile", "roles", "chunks", "wishlist")


@dataclass(frozen=True)
class QueryPart:
    """One question a CV asks, and a name for it a person would recognise."""

    label: str
    text: str


def build_queries(
    style: CVStyle,
    *,
    text: str,
    profile: CVProfile | None = None,
    wishlist: str | None = None,
) -> list[QueryPart]:
    if style not in CV_STYLES:
        raise ValueError(f"unknown CV style {style!r}, expected one of {CV_STYLES}")
    if style == "raw":
        return [QueryPart("the whole CV", text)]
    if style == "chunks":
        pieces = chunk_text(text)
        return [
            QueryPart(f"part {i} of {len(pieces)}", piece)
            for i, piece in enumerate(pieces, 1)
        ]
    if style == "wishlist":
        if wishlist is None:
            raise ValueError("style 'wishlist' needs a written advert (wishlist.py)")
        return [QueryPart("the job you would be hired for next", wishlist)]
    if profile is None:
        raise ValueError(f"style {style!r} needs an extracted profile")
    if style == "profile":
        return [QueryPart("your profile", profile_summary(profile))]
    return _roles(profile)


def profile_summary(profile: CVProfile) -> str:
    """The CV as ten labelled fields, in the shape a vacancy is indexed in.

    Dutch labels, and the same ones `embeddings/documents.py` uses for a vacancy:
    "Vaardigheden" on both sides of the comparison is one less thing for the
    embedding to have to see through.
    """
    years = profile.years_of_experience()
    level = profile.highest_level()
    studies = ", ".join(study.programme for study in profile.education)
    lines = [
        profile.headline,
        _line("Plaats", profile.city),
        _line("Werkervaring", f"{years:g} jaar" if years else None),
        _line("Opleidingsniveau", level),
        _line("Opleiding", studies),
        _line("Vaardigheden", ", ".join(profile.all_skills())),
        _line("Certificaten", ", ".join(profile.certificates)),
        _line("Talen", ", ".join(profile.languages)),
        _line("Werkvorm", profile.desired_work_mode),
        _line("Beschikbaarheid", profile.availability),
    ]
    return "\n".join(line for line in lines if line)


def _roles(profile: CVProfile) -> list[QueryPart]:
    """One part per job, and one for what the CV knows rather than where it worked.

    The education-and-skills part is always there, even for someone with a long
    career: a vacancy that asks for a certificate or a study is answered by that
    part and by nothing else in the CV.
    """
    parts = [
        QueryPart(
            f"job: {job.title}" + (f" @ {job.company}" if job.company else ""),
            _job_text(job),
        )
        for job in profile.experience
    ]
    background = "\n".join(
        line
        for line in (
            _line("Opleiding", ", ".join(s.programme for s in profile.education)),
            _line("Opleidingsniveau", profile.highest_level()),
            _line("Vaardigheden", ", ".join(profile.all_skills())),
            _line("Certificaten", ", ".join(profile.certificates)),
            _line("Talen", ", ".join(profile.languages)),
        )
        if line
    )
    if background:
        parts.append(QueryPart("education and skills", background))
    return parts or [QueryPart("your profile", profile_summary(profile))]


def _job_text(job) -> str:
    lines = [
        job.title,
        _line("Bedrijf", job.company),
        job.summary,
        _line("Vaardigheden", ", ".join(job.skills)),
    ]
    return "\n".join(line for line in lines if line)


def _line(label: str, value) -> str | None:
    return f"{label}: {value}" if value else None
