"""The index, with an embedder simple enough to predict by hand."""

import pytest
from conftest import details as make_details

from joblens.embeddings.index import VacancyIndex
from joblens.extraction.schema import VacancyDetails
from joblens.sources.base import Vacancy


class WordEmbedder:
    """Two dimensions: how much a text is about data, and how much about care."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self.vector(query)

    def vector(self, text: str) -> list[float]:
        low = text.lower()
        return [low.count("data") + 0.01, low.count("zorg") + 0.01]


def vacancy(source_id: str, title: str, text: str) -> Vacancy:
    return Vacancy(
        source="indeed",
        source_id=source_id,
        url="https://example.test",
        title=title,
        company="Voorbeeld",
        text=text,
    )


DATA = vacancy("1", "Data Engineer", "Je bouwt data pipelines met data tooling.")
CARE = vacancy("2", "Verpleegkundige", "Je verleent zorg en meer zorg.")


def details_for(*pairs: tuple[Vacancy, VacancyDetails]) -> dict:
    return {vacancy.key: details for vacancy, details in pairs}


def test_the_closest_vacancy_comes_first():
    index = VacancyIndex.build(
        [CARE, DATA],
        details_for(
            (CARE, make_details("Verpleegkundige")),
            (DATA, make_details("Data Engineer")),
        ),
        WordEmbedder(),
        style="raw",
    )

    matches = index.search("data")

    assert [match.vacancy.key for match in matches] == ["indeed:1", "indeed:2"]
    assert matches[0].score > matches[1].score


def test_top_k_limits_the_results():
    index = VacancyIndex.build([CARE, DATA], {}, WordEmbedder(), style="raw")

    assert len(index.search("zorg", top_k=1)) == 1


def test_documents_are_embedded_once_however_many_queries():
    class CountingEmbedder(WordEmbedder):
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            return super().embed_documents(texts)

    embedder = CountingEmbedder()
    index = VacancyIndex.build([CARE, DATA], {}, embedder, style="raw")

    index.search("data")
    index.search("zorg")

    assert embedder.calls == 1


def test_a_structured_style_leaves_out_what_was_never_extracted():
    index = VacancyIndex.build(
        [CARE, DATA],
        details_for((DATA, make_details("Data Engineer"))),
        WordEmbedder(),
        style="structured_raw",
    )

    assert [v.key for v in index.vacancies] == ["indeed:1"]  # the extracted one
    assert len(index) == 1


def test_the_document_says_what_was_searched():
    index = VacancyIndex.build(
        [DATA],
        details_for((DATA, make_details("Data Engineer", city="Amsterdam"))),
        WordEmbedder(),
        style="structured_raw",
    )

    document = index.search("data")[0].document

    assert document.startswith("Data Engineer\nPlaats: Amsterdam")
    assert "pipelines" in document  # the summary, then the vacancy as published


def test_an_empty_index_answers_nothing():
    assert VacancyIndex.build([], {}, WordEmbedder()).search("data") == []


def test_documents_must_match_their_vacancies():
    with pytest.raises(ValueError, match="1 vacancies but 0 documents"):
        VacancyIndex([DATA], [], WordEmbedder())


def test_documents_too_long_for_the_embedder_are_named():
    """An embedding server truncates silently, so length is checked here."""
    endless = vacancy("3", "Data Engineer", "data " * 6000)
    index = VacancyIndex.build([DATA, endless], {}, WordEmbedder(), style="raw")

    assert [v.key for v in index.oversized()] == ["indeed:3"]
    assert index.oversized(limit=100_000) == []
