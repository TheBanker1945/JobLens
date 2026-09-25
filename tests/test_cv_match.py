"""CV in, ranked vacancies out: the pipeline, with a fake model and a fake index."""

import json

import pytest
from conftest import SAMPLE_CVS, FakeClient
from conftest import details as make_details

from joblens.cv.documents import QueryPart
from joblens.cv.match import (
    CVMatch,
    prepare_cv,
    queries_for,
    rank_cv,
    rank_with_cv,
    search_with_cv,
    shortlist,
    styles_of,
)
from joblens.cv.store import CVCache
from joblens.embeddings.index import VacancyIndex
from joblens.sources.base import Vacancy

PROFILE = {
    "headline": "Verpleegkundige",
    "summary": None,
    "city": "Utrecht",
    "experience": [
        {
            "title": "Verpleegkundige",
            "company": "Altrecht",
            "start": "2022-05",
            "end": None,
            "current": True,
            "summary": "Acute psychiatrie.",
            "skills": ["de-escalatie"],
        }
    ],
    "education": [],
    "skills": ["wondzorg"],
    "certificates": ["BIG-registratie"],
    "languages": ["Dutch"],
    "desired_work_mode": None,
    "availability": None,
}


class TwoWordEmbedder:
    """Two dimensions: how much a text is about zorg, how much about data."""

    def embed_documents(self, texts):
        return [self.vector(text) for text in texts]

    def embed_query(self, query):
        return self.vector(query)

    def vector(self, text):
        low = text.lower()
        return [low.count("zorg") + 0.01, low.count("data") + 0.01]


def vacancy(source_id: str, title: str, text: str) -> Vacancy:
    return Vacancy(
        source="indeed",
        source_id=source_id,
        url="https://example.test",
        title=title,
        company="Voorbeeld",
        text=text,
    )


CARE = vacancy("1", "Verpleegkundige", "zorg zorg zorg voor mensen")
DATA = vacancy("2", "Data Analist", "data data data pipelines")


def index_of(*vacancies) -> VacancyIndex:
    details = {v.key: make_details(v.title) for v in vacancies}
    return VacancyIndex.build(list(vacancies), details, TwoWordEmbedder(), style="raw")


def test_reading_a_cv_costs_one_call_and_the_second_time_costs_none(tmp_path):
    cache = CVCache(tmp_path / "cache.json")
    client = FakeClient(json.dumps(PROFILE))

    first = prepare_cv(
        SAMPLE_CVS / "sanne_vermeulen.md", client, model="fake", cache=cache
    )
    again = prepare_cv(
        SAMPLE_CVS / "sanne_vermeulen.md", FakeClient(), model="fake", cache=cache
    )

    assert first.profile.headline == "Verpleegkundige"
    assert first.from_cache is False
    assert again.from_cache is True  # the second FakeClient had no replies to give
    assert again.profile.city == "Utrecht"


def test_the_model_only_ever_sees_the_redacted_text(tmp_path):
    client = FakeClient(json.dumps(PROFILE))

    prepared = prepare_cv(
        SAMPLE_CVS / "sanne_vermeulen.md",
        client,
        name="Sanne Vermeulen",
        model="fake",
        cache=CVCache(tmp_path / "cache.json"),
    )

    sent = client.calls[0]["messages"][1]["content"]
    assert "sanne.vermeulen@example.com" not in sent
    assert "Sanne" not in sent
    assert sent == prepared.text
    assert "Oudegracht" in prepared.document.text  # the original still has it


def test_a_written_wishlist_is_kept_so_it_is_written_once(tmp_path):
    cache = CVCache(tmp_path / "cache.json")
    prepared = prepare_cv(
        SAMPLE_CVS / "sanne_vermeulen.md",
        FakeClient(json.dumps(PROFILE)),
        model="fake",
        cache=cache,
    )

    first = queries_for(
        prepared,
        "wishlist",
        client=FakeClient("Gezocht: verpleegkundige GGZ"),
        model="fake",
        cache=cache,
    )
    again = queries_for(
        prepared, "wishlist", client=FakeClient(), model="fake", cache=cache
    )

    assert first[0].text == "Gezocht: verpleegkundige GGZ"
    assert again[0].text == first[0].text


def test_a_vacancy_is_ranked_by_the_part_of_the_cv_it_fits_best():
    parts = search_with_cv(
        index_of(CARE, DATA),
        [_part("job: nurse", "zorg voor mensen"), _part("job: analyst", "data")],
    )

    assert [m.vacancy.key for m in parts] == ["indeed:1", "indeed:2"]
    assert parts[0].part.label == "job: nurse"
    assert parts[1].part.label == "job: analyst"


def test_one_part_still_works_and_names_itself():
    matches = search_with_cv(index_of(CARE, DATA), [_part("the whole CV", "zorg")])

    assert matches[0].vacancy.key == "indeed:1"
    assert matches[0].part.label == "the whole CV"


def _part(label: str, text: str) -> QueryPart:
    return QueryPart(label, text)


def test_a_vacancy_past_the_shortlist_is_still_ranked_and_still_scored():
    """What 4.1 changed: `search_with_cv` stops at ten, `rank_with_cv` does not.

    The ones it drops are the rejection nobody can review -- no score, no
    position, no record that they were ever considered.
    """
    many = [
        vacancy(str(n), f"Data Analist {n}", "data pipelines") for n in range(1, 13)
    ]
    parts = [_part("the whole CV", "data")]

    shortlist = search_with_cv(index_of(*many), parts)
    ranked = rank_with_cv(index_of(*many), parts)

    assert len(shortlist) == 10  # the default top_k, and the other two vanish
    assert len(ranked) == 12
    assert [m.vacancy.key for m in ranked[:10]] == [m.vacancy.key for m in shortlist]
    assert all(match.score > 0 for match in ranked)  # including the ones not read


def test_a_style_can_name_several_ways_of_asking():
    assert styles_of("raw") == ["raw"]
    assert styles_of("raw+wishlist") == ["raw", "wishlist"]
    with pytest.raises(ValueError, match="unknown CV style 'vibes'"):
        styles_of("raw+vibes")


def _sanne(tmp_path, advert: str):
    cache = CVCache(tmp_path / "cache.json")
    prepared = prepare_cv(
        SAMPLE_CVS / "sanne_vermeulen.md",
        FakeClient(json.dumps(PROFILE)),
        model="fake",
        cache=cache,
    )
    queries_for(
        prepared, "wishlist", client=FakeClient(advert), model="fake", cache=cache
    )
    return prepared, cache


def test_one_style_ranks_exactly_as_it_did_before(tmp_path):
    prepared, cache = _sanne(tmp_path, "Gezocht: data-analist, data data")
    index = index_of(CARE, DATA)

    alone = rank_cv(index, prepared, "raw", model="fake", cache=cache)
    before = rank_with_cv(index, queries_for(prepared, "raw"))

    assert alone == before


def test_fused_styles_rank_by_position_and_keep_the_part_that_placed_it(tmp_path):
    """The whole CV (a nurse) puts care first; an advert about data puts data
    first. Each is first in one list and second in the other: a dead heat in
    fused points, which keeps the first list's order."""
    prepared, cache = _sanne(tmp_path, "Gezocht: data-analist, data data")
    index = index_of(CARE, DATA)

    fused = rank_cv(index, prepared, "raw+wishlist", model="fake", cache=cache)

    assert [m.vacancy.key for m in fused] == ["indeed:1", "indeed:2"]
    assert fused[0].part.label == "the whole CV"
    assert fused[1].part.label == "the job you would be hired for next"
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)  # rank points
    assert fused[0].score == fused[1].score


# ---------------------------------------------------------------------------
# The shortlist: the head of the ranking, at most `per_employer` per employer.
# On a real CV five near-identical jobs at one employer took five of nine
# judge calls (2026-09-24).


def ranked(*companies: str | None) -> list[CVMatch]:
    return [
        CVMatch(
            Vacancy(
                source="test",
                source_id=str(i),
                url="",
                title=f"Java Developer {i}",
                company=company,
                text="Java.",
            ),
            1.0 - i / 100,
            "document",
            QueryPart("the whole CV", "cv"),
        )
        for i, company in enumerate(companies, 1)
    ]


def keys(matches: list[CVMatch]) -> list[str]:
    return [match.vacancy.source_id for match in matches]


def test_one_employer_cannot_fill_the_shortlist():
    ranking = ranked("Sopra", "Sopra", "Sopra", "Sopra", "Acme", "Globex")

    chosen = shortlist(ranking, 4, per_employer=2)

    assert keys(chosen.matches) == ["1", "2", "5", "6"]
    assert keys(chosen.capped) == ["3", "4"]


@pytest.mark.parametrize(
    "names",
    [
        ("Deloitte Nederland", "Deloitte Netherlands", "Deloitte"),
        (
            "Redwood Software Inc.",
            "Redwood Software Nederland B.V.",
            "Redwood Software",
        ),
    ],
)
def test_one_employer_written_three_ways_is_one_employer(names):
    ranking = ranked(*names, "Acme")

    assert keys(shortlist(ranking, 3, per_employer=2).matches) == ["1", "2", "4"]


def test_an_employer_nobody_names_is_never_capped():
    ranking = ranked(None, None, None, "Acme")

    assert keys(shortlist(ranking, 3, per_employer=1).matches) == ["1", "2", "3"]


def test_the_extracted_employer_counts_when_the_source_names_none():
    """EURES records name no employer; extraction finds it in most texts."""
    ranking = ranked(None, None, "Acme")
    details = {
        m.vacancy.key: make_details("Java", company="Sopra") for m in ranking[:2]
    }

    chosen = shortlist(ranking, 2, per_employer=1, details=details)

    assert keys(chosen.matches) == ["1", "3"]


def test_no_cap_is_the_head_of_the_ranking():
    ranking = ranked("Sopra", "Sopra", "Sopra")

    assert shortlist(ranking, 2, per_employer=0).matches == ranking[:2]
