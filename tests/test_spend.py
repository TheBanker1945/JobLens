"""What JobLens cost this month, per person, on the owner's admin page
(7.10.4): only the owner sees it, last month does not count, and the total on
JobLens's key is the very number the cap for all testers is checked against."""

import pytest
from test_api import app_for

OWNER, ANNA, BRAM = "owner@example.test", "anna@example.test", "bram@example.test"


def paid(database, user, usd: float, paid_by: str = "operator") -> None:
    database.record_usage(
        user.id,
        kind="match",
        model="gemini-3.8-flash",
        prompt_tokens=1000,
        output_tokens=100,
        cost_usd=usd,
        paid_by=paid_by,
    )


@pytest.fixture
def people(database):
    owner = database.create_user(email=OWNER, role="owner", display_name="Mahdi")
    anna = database.create_user(email=ANNA, display_name="Anna")
    bram = database.create_user(email=BRAM)
    paid(database, anna, 0.30)
    paid(database, anna, 0.05)
    paid(database, owner, 0.20)
    database.store_for(bram.id).save_provider_key(
        provider="gemini",
        model="gemini-3.8-flash",
        thinking=False,
        key_secret=b"ciphertext",
        key_hint="ab12",
    )
    paid(database, bram, 0.50, paid_by="own")
    # Last month's call: not this month's money.
    paid(database, anna, 9.00)
    with database.connect() as conn:
        conn.execute(
            "UPDATE usage SET at = now() - interval '40 days' WHERE cost_usd = 9.0"
        )
    return owner, anna, bram


def test_only_the_owner_sees_what_people_cost(database, people, tmp_path):
    with app_for(database, tmp_path, as_=ANNA) as http:
        refused = http.get("/api/admin/spend")

    assert refused.status_code == 403


def test_each_person_this_month_by_whose_key_paid(database, people, tmp_path):
    with app_for(database, tmp_path, as_=OWNER) as http:
        spend = http.get("/api/admin/spend").json()

    rows = [
        (p["email"], round(p["operator_usd"], 2), round(p["own_usd"], 2), p["calls"])
        for p in spend["people"]
    ]
    assert rows == [  # most on JobLens's key first
        (ANNA, 0.35, 0.0, 2),  # last month's $9 left out
        (OWNER, 0.2, 0.0, 1),
        (BRAM, 0.0, 0.5, 1),
    ]
    anna, owner, bram = spend["people"]
    assert anna["allowance_usd"] == 1.0 and owner["allowance_usd"] is None
    assert (anna["own_key"], bram["own_key"]) == (False, True)
    assert spend["tester_allowance_usd"] == 1.0 and spend["operator_cap_usd"] == 10.0


def test_the_total_is_what_the_testers_cap_is_checked_against(
    database, people, tmp_path
):
    """budget.check counts everything on JobLens's key, the owner's use too:
    the page shows that number, and the owner's part of it."""
    with app_for(database, tmp_path, as_=OWNER) as http:
        spend = http.get("/api/admin/spend").json()

    assert spend["operator_usd"] == round(database.spent_this_month()["operator"], 4)
    assert round(spend["operator_usd"], 2) == 0.55
    assert round(spend["owner_usd"], 2) == 0.2
