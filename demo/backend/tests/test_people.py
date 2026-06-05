"""GET /internal/people: returns Persons (resolved + placeholders).
POST /internal/people: creates a placeholder Person."""
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import record_login, require_auth
from app.main import app
from app.models import (
    MeetingInstance,
    MeetingEntry,
    ProjectEntry,
    Person,
    Project,
    ProjectSubscription,
    MeetingSchedule,
)
from tests.conftest import clear_table, db_run, make_person


def _reset():
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(Person)
    clear_table(MeetingSchedule)


def _make_override(email: str):
    async def _override(request: Request):
        request.state.claims = {
            "preferred_username": email.split("@")[0],
            "email": email,
            "group_membership": ["/NikitaPlay"],
        }

    return _override


@pytest.fixture
def make_client():
    def _factory(email: str) -> TestClient:
        app.dependency_overrides[require_auth] = _make_override(email)
        return TestClient(app)

    try:
        yield _factory
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _record(email: str, name: str | None = None):
    async def _do(session):
        await record_login(email, session, display_name=name)

    db_run(_do)


def test_people_lists_resolved_and_placeholders(make_client):
    _reset()
    try:
        _record("alice@x.com", "Alice Anderson")
        make_person("Jachym Doležal", email=None)  # placeholder
        with make_client("alice@x.com") as client:
            r = client.get("/internal/people")
            assert r.status_code == 200, r.text
            names = sorted(p["display_name"] for p in r.json()["people"])
            assert names == ["Alice Anderson", "Jachym Doležal"]
            by_name = {p["display_name"]: p for p in r.json()["people"]}
            assert by_name["Alice Anderson"]["resolved"] is True
            assert by_name["Alice Anderson"]["email"] == "alice@x.com"
            assert by_name["Jachym Doležal"]["resolved"] is False
            assert by_name["Jachym Doležal"]["email"] is None
    finally:
        _reset()


def test_people_search_matches_name_or_email(make_client):
    _reset()
    try:
        _record("alice@x.com", "Alice Anderson")
        _record("bob@y.com", "Bob Brown")
        make_person("Anna", email=None)
        with make_client("alice@x.com") as client:
            # Substring of a display_name (resolved person).
            r = client.get("/internal/people?q=Alice")
            names = {p["display_name"] for p in r.json()["people"]}
            assert names == {"Alice Anderson"}

            # Substring matching a placeholder (no email).
            r = client.get("/internal/people?q=Ann")
            names = {p["display_name"] for p in r.json()["people"]}
            assert names == {"Anna"}

            # Substring of an email — case-insensitive.
            r = client.get("/internal/people?q=BOB@")
            names = {p["display_name"] for p in r.json()["people"]}
            assert names == {"Bob Brown"}
    finally:
        _reset()


def test_create_placeholder(make_client):
    _reset()
    try:
        with make_client("admin@x.com") as client:
            r = client.post(
                "/internal/people", json={"display_name": "Jachym Doležal"}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["display_name"] == "Jachym Doležal"
            assert body["email"] is None
            assert body["resolved"] is False
            assert isinstance(body["id"], int)
    finally:
        _reset()


def test_create_placeholder_rejects_empty(make_client):
    _reset()
    try:
        with make_client("a@x.com") as client:
            r = client.post("/internal/people", json={"display_name": "  "})
            assert r.status_code == 422
            r = client.post("/internal/people", json={})
            assert r.status_code == 422
    finally:
        _reset()
