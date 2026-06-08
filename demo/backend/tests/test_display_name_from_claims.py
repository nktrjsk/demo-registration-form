"""The OIDC → display_name claim chain.

Realms vary in which claims they populate. `name` is the OIDC standard
but some Keycloak setups only fill `given_name`/`family_name`, and the
fallback used to be `preferred_username` (often the email), which made
the people search useless. The chain now tries name → given+family →
preferred_username so a usable human name lands in the Person row
wherever the realm exposes one.
"""
from app.auth import _display_name_from_claims


def test_prefers_name_claim():
    assert _display_name_from_claims({
        "name": "Alice Anderson",
        "given_name": "Alice",
        "family_name": "Anderson",
        "preferred_username": "alice",
    }) == "Alice Anderson"


def test_falls_back_to_given_and_family():
    assert _display_name_from_claims({
        "given_name": "Alice",
        "family_name": "Anderson",
        "preferred_username": "alice@test.example",
    }) == "Alice Anderson"


def test_uses_given_only_if_family_missing():
    assert _display_name_from_claims({
        "given_name": "Alice",
        "preferred_username": "alice@test.example",
    }) == "Alice"


def test_uses_family_only_if_given_missing():
    assert _display_name_from_claims({
        "family_name": "Anderson",
        "preferred_username": "alice@test.example",
    }) == "Anderson"


def test_falls_back_to_preferred_username():
    assert _display_name_from_claims({
        "preferred_username": "alice",
    }) == "alice"


def test_empty_strings_treated_as_missing():
    assert _display_name_from_claims({
        "name": "  ",
        "given_name": "Alice",
        "family_name": "",
        "preferred_username": "alice@test.example",
    }) == "Alice"


def test_no_claims_returns_none():
    assert _display_name_from_claims({}) is None
