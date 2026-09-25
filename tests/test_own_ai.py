"""Bringing your own model, and the allowance on JobLens's (7.6).

Against the test database through the real app in-process; model calls are
fakes. Skipped without the Docker database, except the vault and presets.
"""

import json

import httpx
import openai
import pytest
from conftest import FakeClient
from cryptography.fernet import Fernet
from test_api import app_for, upload
from test_cv_match import PROFILE
from test_service_matching import JUDGEMENT, WISHLIST

from joblens.llm.presets import PRESETS, available
from joblens.service.budget import Budgets
from joblens.vault import Vault, VaultError

EMAIL = "lisa@example.test"
KEY = "sk-test-0123456789abcdef-a1b2"


@pytest.fixture
def vault():
    return Vault(Fernet.generate_key().decode())


@pytest.fixture
def lisa(database):
    return database.create_user(email=EMAIL)


def works(settings):
    """A provider that answers the test call."""


def refuses(settings):
    raise_refusal(401)


def raise_refusal(status: int):
    from joblens.service.matching import provider_errors

    with provider_errors():
        raise openai.APIStatusError(
            "Incorrect API key",
            response=httpx.Response(status, request=httpx.Request("POST", "http://x")),
            body=None,
        )


# -- the vault and the list -------------------------------------------------


def test_a_key_is_stored_as_ciphertext_and_only_this_server_can_read_it(vault):
    sealed = vault.lock(KEY)

    assert KEY.encode() not in sealed
    assert vault.unlock(sealed) == KEY
    with pytest.raises(VaultError):
        Vault(Fernet.generate_key().decode()).unlock(sealed)
    with pytest.raises(ValueError, match="not a valid key"):
        Vault("too short")
    assert Vault.from_env({}) is None  # no secret: own keys are off


def test_a_hosted_server_offers_no_local_models_and_only_https_addresses():
    hosted = available(allow_local=False)

    assert {one.provider for one in hosted} == {
        "gemini",
        "openai",
        "anthropic",
        "openrouter",
        "deepseek",
    }
    assert all(one.base_url.startswith("https://") for one in hosted)
    assert PRESETS["ollama"].local and PRESETS["ollama"] not in hosted


# -- bringing a key -----------------------------------------------------------


def test_without_a_server_secret_nobody_can_bring_a_key(database, lisa, tmp_path):
    with app_for(database, tmp_path) as http:
        answer = http.put(
            "/api/ai",
            json={"provider": "gemini", "model": "gemini-3.8-flash", "api_key": KEY},
        )

    assert answer.status_code == 503


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"provider": "evil", "model": "m"}, "not a provider"),
        ({"provider": "ollama", "model": "qwen3:8b"}, "not a provider"),  # hosted
        ({"provider": "openai", "model": "gpt-latest"}, "latest"),
    ],
)
def test_a_key_for_what_this_server_does_not_offer_is_refused(
    database, lisa, tmp_path, vault, given, said
):
    with app_for(database, tmp_path, vault=vault, check_provider=works) as http:
        answer = http.put("/api/ai", json=given | {"api_key": KEY})

    assert answer.status_code == 400 and said in answer.json()["detail"]


def test_a_key_the_provider_refuses_is_not_kept(database, lisa, tmp_path, vault):
    with app_for(database, tmp_path, vault=vault, check_provider=refuses) as http:
        answer = http.put(
            "/api/ai", json={"provider": "openai", "model": "gpt-x", "api_key": KEY}
        )
        after = http.get("/api/ai").json()

    assert answer.status_code == 400
    assert "refused this key or model (401)" in answer.json()["detail"]
    assert "Nothing was saved" in answer.json()["detail"]
    assert after["using"] == "joblens"


def test_a_working_key_is_kept_encrypted_and_never_shown_again(
    database, lisa, tmp_path, vault
):
    with app_for(database, tmp_path, vault=vault, check_provider=works) as http:
        saved = http.put(
            "/api/ai",
            json={"provider": "gemini", "model": "gemini-3.8-flash", "api_key": KEY},
        )
        shown = http.get("/api/ai")
        exported = http.get("/api/me/export")

    assert saved.status_code == 200
    assert shown.json()["using"] == "own"
    assert shown.json()["key_hint"] == "...a1b2"
    assert "JobLens's default" in shown.json()["measured"]
    for answer in (saved, shown, exported):
        assert KEY not in answer.text
    with database.connect() as conn:
        stored = conn.execute("SELECT key_secret FROM provider_keys").fetchone()
    assert KEY.encode() not in bytes(stored["key_secret"])


def test_a_match_runs_on_the_persons_own_model_and_is_recorded_as_theirs(
    database, lisa, tmp_path, vault
):
    asked_with = []
    client = FakeClient(json.dumps(PROFILE), WISHLIST, JUDGEMENT, JUDGEMENT, JUDGEMENT)

    def chat(settings):
        asked_with.append((settings.provider, settings.model, settings.api_key))
        return client

    with app_for(
        database, tmp_path, chat=chat, vault=vault, check_provider=works
    ) as http:
        http.put(
            "/api/ai", json={"provider": "openai", "model": "gpt-x", "api_key": KEY}
        )
        upload(http)
        job = http.post("/api/matches").json()

    assert job["status"] == "done"
    assert set(asked_with) == {("openai", "gpt-x", KEY)}  # decrypted, and theirs
    with database.connect() as conn:
        paid = conn.execute("SELECT kind, paid_by, model FROM usage").fetchall()
    assert {(row["kind"], row["paid_by"], row["model"]) for row in paid} == {
        ("cv", "own", "gpt-x"),
        ("match", "own", "gpt-x"),
    }


def test_forgetting_a_key_goes_back_to_joblens(database, lisa, tmp_path, vault):
    with app_for(database, tmp_path, vault=vault, check_provider=works) as http:
        http.put(
            "/api/ai", json={"provider": "openai", "model": "gpt-x", "api_key": KEY}
        )
        assert http.delete("/api/ai").status_code == 204
        assert http.get("/api/ai").json()["using"] == "joblens"


# -- the allowance on JobLens's key ------------------------------------------


def spend(database, email, usd, paid_by="operator"):
    database.record_usage(
        database.user_by_email(email).id,
        kind="match",
        model="gemini-3.8-flash",
        prompt_tokens=1,
        output_tokens=1,
        cost_usd=usd,
        paid_by=paid_by,
    )


def test_a_tester_past_their_allowance_is_stopped_and_told_how_to_go_on(
    database, lisa, tmp_path
):
    spend(database, EMAIL, 0.999)
    client = FakeClient(json.dumps(PROFILE))
    with app_for(database, tmp_path, chat=lambda s: client) as http:
        refused = upload(http)
        usage = http.get("/api/usage").json()

    assert refused.status_code == 402
    assert "own API key" in refused.json()["detail"]
    assert usage["allowance_usd"] == 1.0 and usage["left_usd"] == pytest.approx(0.001)


def test_all_testers_together_have_a_cap(database, lisa, tmp_path):
    database.create_user(email="sanne@example.test")
    spend(database, "sanne@example.test", 9.999)
    with app_for(database, tmp_path) as http:
        refused = upload(http)

    assert refused.status_code == 402
    assert "all testers" in refused.json()["detail"]


def test_the_owner_and_own_keys_are_never_stopped(database, tmp_path, vault):
    owner = database.create_user(email="mahdi@example.test", role="owner")
    database.create_user(email=EMAIL)
    spend(database, owner.email, 50.0)
    spend(database, EMAIL, 50.0)
    profile = json.dumps(PROFILE)

    with app_for(
        database, tmp_path, chat=lambda s: FakeClient(profile), as_=owner.email
    ) as http:
        assert upload(http).status_code == 201
        assert http.get("/api/usage").json()["allowance_usd"] is None

    with app_for(
        database,
        tmp_path,
        chat=lambda s: FakeClient(profile),
        vault=vault,
        check_provider=works,
    ) as http:
        http.put(
            "/api/ai", json={"provider": "openai", "model": "gpt-x", "api_key": KEY}
        )
        assert upload(http).status_code == 201


def test_budgets_come_from_the_environment():
    budgets = Budgets.from_env(
        {"JOBLENS_TESTER_MONTHLY_USD": "2.5", "JOBLENS_OPERATOR_MONTHLY_USD": "20"}
    )

    assert (budgets.tester_monthly_usd, budgets.operator_monthly_usd) == (2.5, 20.0)


def test_deleting_a_person_deletes_their_key_and_their_spending(database, vault):
    user = database.create_user(email=EMAIL)
    store = database.store_for(user.id)
    store.save_provider_key(
        provider="openai",
        model="gpt-x",
        thinking=False,
        key_secret=vault.lock(KEY),
        key_hint="a1b2",
    )
    spend(database, EMAIL, 0.1)

    database.delete_user(user.id)

    with database.connect() as conn:
        for table in ("provider_keys", "usage"):
            assert (
                conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
            )
