"""schema.org JobPosting from a page: the variants real career sites send."""

import json
from datetime import UTC, datetime

from joblens.sources.jsonld import job_postings, place_of, posting_to_vacancy

NOW = datetime(2026, 9, 23, 3, 0, tzinfo=UTC)
TEXT = "<p>Je bouwt services in Python voor ons team in Delft. " * 6 + "</p>"


def page(*blocks) -> str:
    texts = [b if isinstance(b, str) else json.dumps(b) for b in blocks]
    scripts = "".join(
        f'<script type="application/ld+json">{text}</script>' for text in texts
    )
    return f"<html><head>{scripts}</head><body>Vacature</body></html>"


def posting(**fields) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Python Developer",
        "description": TEXT,
        "datePosted": "2026-09-04",
        "hiringOrganization": {"@type": "Organization", "name": "Werkenbij BV"},
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": "Delft", "addressCountry": "NL"},
        },
        **fields,
    }


def test_a_posting_at_the_top_level_in_a_list_or_in_a_graph():
    graph = {
        "@context": "https://schema.org",
        "@graph": [{"@type": "WebPage"}, posting()],
    }

    assert len(job_postings(page(posting()))) == 1
    assert len(job_postings(page([{"@type": "Organization"}, posting()]))) == 1
    assert len(job_postings(page(graph))) == 1
    assert len(job_postings(page(posting(**{"@type": ["JobPosting", "Thing"]})))) == 1


def test_a_broken_block_does_not_hide_a_good_one():
    assert len(job_postings(page("{not json,", posting()))) == 1


def test_no_posting_on_an_ordinary_page():
    assert job_postings(page({"@type": "WebPage"})) == []


def test_a_posting_becomes_a_vacancy():
    vacancy = posting_to_vacancy(
        posting(identifier={"@type": "PropertyValue", "value": "V-123"}),
        source="careersite",
        url="https://werkenbij.example.nl/vacatures/python-developer",
        now=NOW,
    )

    assert vacancy.key == "careersite:V-123"
    assert vacancy.title == "Python Developer"
    assert vacancy.company == "Werkenbij BV"
    assert vacancy.city == "Delft"
    assert vacancy.country == "NL"
    assert vacancy.posted_at == datetime(2026, 9, 4, tzinfo=UTC)
    assert vacancy.raw["location"] == "Delft"
    assert "Python" in vacancy.text


def test_the_variants_of_the_fields():
    variant = posting(
        hiringOrganization="Gemeente Delft",
        jobLocation=[
            {
                "address": {
                    "addressRegion": "Zuid-Holland",
                    "addressCountry": {"name": "NL"},
                }
            },
            {"address": {"addressLocality": "Den Haag"}},
        ],
        identifier="12345",
        datePosted="2026-09-04T08:00:00+02:00",
    )

    vacancy = posting_to_vacancy(variant, source="careersite", url="u", now=NOW)

    assert vacancy.company == "Gemeente Delft"
    assert vacancy.source_id == "12345"
    assert vacancy.city == "Den Haag"
    assert vacancy.raw["location"] == "Zuid-Holland, Den Haag"
    assert vacancy.posted_at.utcoffset().total_seconds() == 7200


def test_remote_work_is_said_so():
    remote = posting(jobLocation=None, jobLocationType="TELECOMMUTE")

    assert place_of(remote) == (None, "Remote", None)


def test_a_teaser_is_not_a_vacancy():
    """werkenvoornederland.nl, 2026-09-22: a 104-character description."""
    teaser = posting(description="<p>Ben jij een engineer die Kubernetes bouwt?</p>")

    assert posting_to_vacancy(teaser, source="careersite", url="u", now=NOW) is None


def test_a_posting_the_employer_closed_is_not_a_vacancy():
    expired = posting(validThrough="2026-09-01")

    assert posting_to_vacancy(expired, source="careersite", url="u", now=NOW) is None


def test_without_an_identifier_the_page_names_it():
    one = posting_to_vacancy(posting(), source="careersite", url="https://a/1", now=NOW)
    again = posting_to_vacancy(
        posting(), source="careersite", url="https://a/1", now=NOW
    )
    other = posting_to_vacancy(
        posting(), source="careersite", url="https://a/2", now=NOW
    )

    assert one.source_id == again.source_id != other.source_id


def test_contact_details_in_the_text_go():
    mailed = posting(
        description=TEXT + "<p>Mail jan@werkenbij.nl of bel 06-12345678</p>"
    )

    vacancy = posting_to_vacancy(mailed, source="careersite", url="u", now=NOW)

    assert "jan@werkenbij.nl" not in vacancy.text
    assert "06-12345678" not in vacancy.text
