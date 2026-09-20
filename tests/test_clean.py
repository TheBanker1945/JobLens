from joblens.sources.clean import html_to_text, strip_contact_details, to_clean_text


def test_html_becomes_readable_text():
    html = (
        "<div><h2>Wat ga je doen?</h2><p>Je bouwt dashboards.</p>"
        "<ul><li>SQL</li><li>Power&nbsp;BI</li></ul>"
        "<p>Solliciteer<br>vandaag!</p></div>"
    )

    text = html_to_text(html)

    assert "Wat ga je doen?" in text
    assert "- SQL" in text and "- Power BI" in text  # &nbsp; became a space
    assert "<" not in text
    assert "Solliciteer\nvandaag!" in text


def test_no_runaway_blank_lines():
    assert html_to_text("<p>a</p><div></div><div></div><p>b</p>") == "a\n\nb"


def test_emails_and_phone_numbers_are_removed():
    text = (
        "Vragen? Mail anna.de.vries@bedrijf.nl of bel 06-12345678. "
        "Ook bereikbaar op +31 70 700 0510 of 070 7000510."
    )

    cleaned = strip_contact_details(text)

    assert "@" not in cleaned
    assert "12345678" not in cleaned
    assert "7000510" not in cleaned
    assert cleaned.count("[phone removed]") == 3
    assert "[email removed]" in cleaned


def test_normal_numbers_are_kept():
    text = "Salaris 3200 - 3800 euro, 32 uur per week, 25 vakantiedagen in 2026."

    assert strip_contact_details(text) == text


def test_clean_text_does_both():
    cleaned = to_clean_text("<p>Bel <b>06 12345678</b> of mail jan@x.nl</p>")

    assert cleaned == "Bel [phone removed] of mail [email removed]"


def test_international_numbers_without_leading_zero():
    text = "Bel +31 6 12345678 of +31 (0)70 700 0510"

    assert strip_contact_details(text) == "Bel [phone removed] of [phone removed]"


def test_years_and_amounts_survive():
    text = "In 2026 zoeken we 3 mensen, salaris 3200 - 3800, 40 uur, postcode 1234 AB."

    assert strip_contact_details(text) == text
