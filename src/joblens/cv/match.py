"""A CV in, ranked vacancies out.

Three steps, each already built: read and redact the file (read.py, clean.py),
extract a profile (extract.py), then turn that into one or more queries
(documents.py) and ask the index (embeddings/index.py). Nothing here is a second
search system -- `VacancyIndex` is the same object `scripts/search_vacancies.py`
uses, holding the same vectors, so a CV and a typed query are compared against
identical documents.

What is CV-specific is that a search can have several parts, and that the part
that won is worth keeping: "this matched your Coolblue job" is the beginning of
an explanation, and 3.6 turns it into one.
"""

from dataclasses import dataclass
from pathlib import Path

from joblens.cv.clean import Redacted, redact_cv
from joblens.cv.documents import CV_STYLES, CVStyle, QueryPart, build_queries
from joblens.cv.extract import DroppedDate, extract_cv, verify_dates
from joblens.cv.read import CVDocument, read_cv
from joblens.cv.schema import CVProfile
from joblens.cv.store import CVCache
from joblens.cv.wishlist import write_ideal_vacancy
from joblens.embeddings.index import VacancyIndex
from joblens.embeddings.similarity import fuse_orders
from joblens.llm.structured import Mode
from joblens.llm.types import ChatClient
from joblens.sources.base import Vacancy


@dataclass(frozen=True)
class PreparedCV:
    """Everything a CV is once it has been read, and nothing it was before."""

    name: str  # the file stem: what the eval and the labels call this CV
    document: CVDocument
    redacted: Redacted
    profile: CVProfile  # dates checked against the CV text; see extract.verify_dates
    dropped_dates: tuple[DroppedDate, ...] = ()
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    from_cache: bool = False

    @property
    def text(self) -> str:
        """The redacted text: the only version of the CV that leaves the machine."""
        return self.redacted.text


# How a CV asks the index, unless told otherwise: the whole redacted CV and the
# advert a model writes from its profile, each ranking every vacancy, fused by
# position (reciprocal rank fusion, embeddings/similarity.py).
#
# Measured 2026-09-22 on 400 vacancies, nDCG@10 (evals/cv-matching.toml):
#   raw alone, the default since 3.5:  worst CV 0.29, the real CV 0.29
#   raw + wishlist:                    worst CV 0.51, the real CV 0.52
# The rule it had to pass was written down before the run. `--style raw` is
# the 3.5 behaviour, one flag away.
DEFAULT_STYLE = "raw+wishlist"


@dataclass(frozen=True)
class CVMatch:
    vacancy: Vacancy
    # What it was ranked on: a cosine for one style, or fused rank points
    # (a sum of 1 / (60 + position)) when several styles were fused.
    score: float
    document: str  # the vacancy text that was embedded
    part: QueryPart  # the piece of the CV that matched it best


def prepare_cv(
    path: Path,
    client: ChatClient,
    *,
    name: str | None = None,
    model: str = "unknown",
    mode: Mode | None = None,
    cache: CVCache | None = None,
) -> PreparedCV:
    """Read, redact and extract. `cache` makes the second run free."""
    document = read_cv(path)
    redacted = redact_cv(document.text, name=name)
    # What the model answered is what gets cached, exactly as 3.6 stores the
    # judge's raw answer: the check is cheap and re-running it over an old entry
    # is how a change to `verify_dates` is measured without paying again.
    if cache and (hit := cache.get("profile", model, redacted.text)):
        checked = verify_dates(CVProfile.model_validate(hit), redacted.text)
        return PreparedCV(
            name=path.stem,
            document=document,
            redacted=redacted,
            profile=checked.profile,
            dropped_dates=tuple(checked.dropped),
            from_cache=True,
        )
    result = extract_cv(redacted.text, client, mode=mode or "schema")
    if cache:
        cache.put(
            "profile", model, redacted.text, result.details.model_dump(mode="json")
        )
    checked = verify_dates(result.details, redacted.text)
    return PreparedCV(
        name=path.stem,
        document=document,
        redacted=redacted,
        profile=checked.profile,
        dropped_dates=tuple(checked.dropped),
        prompt_tokens=result.prompt_tokens,
        output_tokens=result.output_tokens,
        latency_s=result.latency_s,
    )


def queries_for(
    prepared: PreparedCV,
    style: CVStyle,
    *,
    client: ChatClient | None = None,
    model: str = "unknown",
    cache: CVCache | None = None,
) -> list[QueryPart]:
    """The parts this CV searches with, writing the wishlist advert if needed."""
    wishlist = None
    if style == "wishlist":
        wishlist = _wishlist(prepared, client, model, cache)
    return build_queries(
        style, text=prepared.text, profile=prepared.profile, wishlist=wishlist
    )


def search_with_cv(
    index: VacancyIndex,
    parts: list[QueryPart],
    *,
    top_k: int = 10,
    instruction: bool = True,
) -> list[CVMatch]:
    matches = index.search_many(
        [part.text for part in parts], top_k=top_k, instruction=instruction
    )
    return [
        CVMatch(match.vacancy, match.score, match.document, parts[match.query_index])
        for match in matches
    ]


def rank_with_cv(
    index: VacancyIndex, parts: list[QueryPart], *, instruction: bool = True
) -> list[CVMatch]:
    """Every vacancy in the index, best first. The shortlist is its head.

    Retrieval always ranked the whole corpus -- `rank_pooled` scores all of it
    and then throws away everything past `top_k`. Until 4.1 that is where 267 of
    279 vacancies went: rejected with no score, no record and no reason, which is
    the one rejection you cannot review afterwards. It costs nothing to keep. The
    vectors are already in memory, so this is the same arithmetic and not one
    extra API call.
    """
    return search_with_cv(index, parts, top_k=len(index), instruction=instruction)


def styles_of(style: str) -> list[CVStyle]:
    """ "raw+wishlist" -> ["raw", "wishlist"]. One style is a list of one."""
    names = style.split("+")
    unknown = [name for name in names if name not in CV_STYLES]
    if unknown:
        raise ValueError(
            f"unknown CV style {'+'.join(unknown)!r}: use one of "
            f"{', '.join(CV_STYLES)}, or several joined by '+'"
        )
    return names  # type: ignore[return-value] - checked against CV_STYLES above


def rank_cv(
    index: VacancyIndex,
    prepared: PreparedCV,
    style: str = DEFAULT_STYLE,
    *,
    client: ChatClient | None = None,
    model: str = "unknown",
    cache: CVCache | None = None,
) -> list[CVMatch]:
    """Every vacancy in the index, best first, for one style or several fused.

    Each style ranks the whole corpus on its own; fusion then orders the
    vacancies by where those lists put them, never by comparing a cosine from
    one list with a cosine from the other. A fused match keeps the part of the
    CV from the list that placed it highest, so it can still say what it
    answered.
    """
    lists = [
        rank_with_cv(
            index, queries_for(prepared, one, client=client, model=model, cache=cache)
        )
        for one in styles_of(style)
    ]
    if len(lists) == 1:
        return lists[0]
    by_key = [{match.vacancy.key: match for match in one} for one in lists]
    fused = fuse_orders([[match.vacancy.key for match in one] for one in lists])
    return [
        CVMatch(
            vacancy=by_key[one.best_list][one.item].vacancy,
            score=one.points,
            document=by_key[one.best_list][one.item].document,
            part=by_key[one.best_list][one.item].part,
        )
        for one in fused
    ]


def _wishlist(
    prepared: PreparedCV,
    client: ChatClient | None,
    model: str,
    cache: CVCache | None,
) -> str:
    key = prepared.text
    if cache and (hit := cache.get("wishlist", model, key)):
        return hit["text"]
    if client is None:
        raise ValueError("style 'wishlist' needs a client to write the advert")
    advert = write_ideal_vacancy(prepared.profile, client)
    if cache:
        cache.put("wishlist", model, key, {"text": advert})
    return advert
