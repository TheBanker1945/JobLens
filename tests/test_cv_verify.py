"""The quote check against text that came out of a real PDF.

Invented content, real shapes: every source line below reproduces what pypdf
made of a LaTeX-typeset CV on 2026-09-24.
"""

import pytest

from joblens.cv.verify import quoted, searchable


@pytest.mark.parametrize(
    ("source", "quote"),
    [
        # the typesetter split a word: read without the hyphen
        ("Als developer bij Cool-\nblue werkte ik aan de app", "bij Coolblue werkte"),
        ("we help or-\nganizations plan their routes", "we help organizations"),
        (
            "a minor in ma-\nchine learning at a hogeschool",
            "a minor in machine learning",
        ),
        # a real hyphen that fell at a line end: read with it
        ("built a One-\nClass classifier for fraud", "a One-Class classifier"),
        # quoted as it stands on the page: still fine
        ("in overeen-\nstemming met de AVG", "in overeen- stemming met de AVG"),
        # a quote that stops at the break, as the text has it
        ("joined our consul-\ntancy branch", "joined our consul-"),
        # a ligature, as LaTeX writes it
        ("refactoring legacy code or ﬁnding bugs", "or finding bugs"),
    ],
)
def test_a_quote_of_what_the_page_shows_is_found(source, quote):
    assert quoted(quote, searchable(source))


@pytest.mark.parametrize(
    ("source", "quote"),
    [
        # a hyphen typed inside a line is kept: only a line end has two readings
        ("Vijf jaar als Java-ontwikkelaar", "als Javaontwikkelaar"),
        # the two readings are the only two: a different word still fails
        ("we help or-\nganizations plan", "we help organisations plan"),
        # a dash between numbers or around a space is no line-end hyphen
        ("Data-analist 2019 -\n2021 bij Coolblue", "Data-analist 20192021"),
        # the old guarantees hold: whole words only
        ("Stack: JavaScript, TypeScript", "Java"),
    ],
)
def test_nothing_that_is_not_on_the_page_is_found(source, quote):
    assert not quoted(quote, searchable(source))
