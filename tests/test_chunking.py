"""Splitting a vacancy into pieces small enough to mean one thing."""

import pytest

from joblens.embeddings.documents import chunk_text


def paragraphs(*lengths: int) -> str:
    return "\n\n".join("w" * (n - 1) + "." for n in lengths)


def test_a_short_text_stays_one_chunk():
    assert chunk_text("Korte vacature.", size=900, overlap=150) == ["Korte vacature."]


def test_paragraphs_are_packed_until_they_no_longer_fit():
    text = paragraphs(300, 300, 300)

    chunks = chunk_text(text, size=700, overlap=0)

    assert len(chunks) == 2
    assert chunks[0].count(".") == 2  # two paragraphs fitted, the third did not


def test_a_paragraph_longer_than_a_chunk_is_cut():
    chunks = chunk_text("word " * 500, size=400, overlap=50)

    assert len(chunks) > 1


def test_no_chunk_exceeds_the_size_plus_its_carried_over_tail():
    text = paragraphs(*([250] * 40))

    for chunk in chunk_text(text, size=600, overlap=100):
        assert len(chunk) <= 700


def test_the_overlap_carries_the_tail_of_one_chunk_into_the_next():
    chunks = chunk_text(paragraphs(400, 400, 400), size=500, overlap=120)

    assert chunks[1].startswith(chunks[0][-120:].strip()[:40])


def test_every_chunk_has_content():
    text = "Eerste alinea.\n\n\n\n   \n\nTweede alinea.\n\n"

    assert all(chunk.strip() for chunk in chunk_text(text, size=100, overlap=20))


def test_an_impossible_configuration_is_refused():
    with pytest.raises(ValueError):
        chunk_text("tekst", size=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_text("tekst", size=0)
