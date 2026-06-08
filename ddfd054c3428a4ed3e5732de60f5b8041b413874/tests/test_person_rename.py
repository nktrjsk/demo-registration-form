"""Admin-only PATCH /people/{id} for fixing display_name.

Realms that don't populate the OIDC `name`/`given_name`/`family_name`
claims leave the auto-created Person rows with email as display_name,
making the leader picker unusable for name search. The rename endpoint
lets an admin curate those names in-place. Subsequent OIDC logins must
not stomp the curated value (covered in test_person_login.py).
"""
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import require_auth
from app.main import app
from app.models import (
    MeetingEntry,
    MeetingInstance,
    Person,
    Project,
    ProjectEntry,
    ProjectSubscription,
)
from tests.conftest import clear_table, make_person


def _make_override(groups: list[str]):
    async def _override(request: Request):
        request.state.claims = {
            "preferred_username": "test-user",
            "email": "user@test.example",
            "group_membership": groups,
        }
    return _override


def _reset():
    # Person is FK'd from Project (leader_person_id); clear dependents
    # before Person so the delete doesn't trip the constraint.
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(Person)


@pytest.fixture
def admin_client():
    app.dependency_overrides[require_auth] = _make_override(
        ["/NikitaPlay", "/NikitaPlay/admin"]
    )
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


@pytest.fixture
def member_client():
    app.dependency_overrides[require_auth] = _make_override(["/NikitaPlay"])
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_admin_rename_updates_display_name(admin_client):
    _reset()
    try:
        pid = make_person("alice@test.example", email="alice@test.example")
        r = admin_client.patch(
            f"/internal/people/{pid}",
            json={"display_name": "Alice Anderson"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["display_name"] == "Alice Anderson"

        # Persisted, not just echoed.
        r = admin_client.get("/internal/people")
        names = {p["id"]: p["display_name"] for p in r.json()["people"]}
        assert names[pid] == "Alice Anderson"
    finally:
        _reset()


def test_non_admin_rename_is_rejected(member_client):
    _reset()
    try:
        pid = make_person("alice@test.example", email="alice@test.example")
        r = member_client.patch(
            f"/internal/people/{pid}",
            json={"display_name": "Hacked"},
        )
        assert r.status_code == 403, r.text

        # Original value untouched.
        r = member_client.get("/internal/people")
        names = {p["id"]: p["display_name"] for p in r.json()["people"]}
        assert names[pid] == "alice@test.example"
    finally:
        _reset()


def test_rename_rejects_empty_name(admin_client):
    _reset()
    try:
        pid = make_person("alice@test.example", email="alice@test.example")
        r = admin_client.patch(
            f"/internal/people/{pid}",
            json={"display_name": "   "},
        )
        assert r.status_code == 422, r.text
    finally:
        _reset()


def test_rename_unknown_person_returns_404(admin_client):
    _reset()
    try:
        r = admin_client.patch(
            "/internal/people/999999",
            json={"display_name": "Nobody"},
        )
        assert r.status_code == 404, r.text
    finally:
        _reset()
