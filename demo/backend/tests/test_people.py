"""GET /internal/people: roster-derived picker source for the leader field."""
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import record_login, require_auth
from app.main import app
from app.models import (
    MeetingInstance,
    MeetingEntry,
    ProjectEntry,
    Project,
    ProjectSubscription,
    UserRoster,
    MeetingSchedule,
)
from tests.conftest import clear_table, db_run


def _reset():
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(UserRoster)
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


def _record(email: str):
    async def _do(session):
        await record_login(email, session)

    db_run(_do)


def test_people_lists_roster_emails(make_client):
    _reset()
    try:
        for email in ["alice@x.com", "bob@y.com", "carol@z.com"]:
            _record(email)
        with make_client("alice@x.com") as client:
            r = client.get("/internal/people")
            assert r.status_code == 200, r.text
            assert set(r.json()["people"]) == {
                "alice@x.com",
                "bob@y.com",
                "carol@z.com",
            }
    finally:
        _reset()


def test_people_search_substring_case_insensitive(make_client):
    _reset()
    try:
        for email in ["alice@x.com", "bob@y.com", "ALICEson@z.com"]:
            _record(email)
        with make_client("alice@x.com") as client:
            r = client.get("/internal/people?q=alice")
            people = set(r.json()["people"])
            assert "alice@x.com" in people
            assert "ALICEson@z.com" in people
            assert "bob@y.com" not in people
    finally:
        _reset()


def test_people_excludes_never_logged_in(make_client):
    """Same constraint as the attendee list: only OIDC-authenticated users."""
    _reset()
    try:
        _record("real@x.com")
        with make_client("real@x.com") as client:
            r = client.get("/internal/people?q=ghost")
            assert r.json()["people"] == []
    finally:
        _reset()
