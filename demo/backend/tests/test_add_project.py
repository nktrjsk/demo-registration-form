"""REQ-006: any signed-in user can add a project to the current meeting.

- AI-025: a non-admin user can POST a new project.
- AI-026: a project added by one user shows up for all other users.
- AI-027: a project added on meeting A does NOT retroactively appear
  on a different meeting B.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from app.auth import require_auth
from app.main import app
from app.models import (
    MeetingInstance,
    MeetingEntry,
    ProjectEntry,
    Project,
    UserRoster,
)
from tests.conftest import clear_table, db_run


ALICE = "alice@test.example"
BOB = "bob@test.example"


def _reset():
    clear_table(ProjectEntry)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(UserRoster)


def _seed_meeting(meeting_date: date) -> int:
    async def _do(session):
        m = MeetingInstance(meeting_date=meeting_date)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m.id

    return db_run(_do)


def _make_override(email: str, admin: bool = False):
    groups = ["/NikitaPlay"]
    if admin:
        groups.append("/NikitaPlay/admin")

    async def _override(request: Request):
        request.state.claims = {
            "preferred_username": email.split("@")[0],
            "email": email,
            "group_membership": groups,
        }

    return _override


@pytest.fixture
def make_client():
    def _factory(email: str, admin: bool = False) -> TestClient:
        app.dependency_overrides[require_auth] = _make_override(email, admin)
        return TestClient(app)

    try:
        yield _factory
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_non_admin_can_add_project(make_client):
    """AI-025."""
    _reset()
    try:
        meeting_id = _seed_meeting(date.today())
        with make_client(ALICE, admin=False) as client:
            r = client.post(
                f"/internal/meeting/{meeting_id}/projects",
                json={"name": "CETIN", "leader": "Jachym Doležal"},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["name"] == "CETIN"
            assert data["leader"] == "Jachym Doležal"
    finally:
        _reset()


def test_added_project_visible_to_other_users(make_client):
    """AI-026: a project added by one user shows up for another user."""
    _reset()
    try:
        meeting_id = _seed_meeting(date.today())
        with make_client(ALICE) as alice_client:
            alice_client.post(
                f"/internal/meeting/{meeting_id}/projects",
                json={"name": "Medin", "leader": "Timothy Hobbs"},
            )

        with make_client(BOB) as bob_client:
            r = bob_client.get("/internal/meeting/current")
            assert r.status_code == 200
            projects = r.json()["meeting"]["projects"]
            assert [p["name"] for p in projects] == ["Medin"]
            assert projects[0]["leader"] == "Timothy Hobbs"
    finally:
        _reset()


def test_project_scoped_to_meeting(make_client):
    """AI-027: a project added on meeting A does not appear on meeting B."""
    _reset()
    try:
        m_old = _seed_meeting(date(2026, 5, 25))
        m_new = _seed_meeting(date(2026, 6, 1))

        with make_client(ALICE) as client:
            client.post(
                f"/internal/meeting/{m_new}/projects",
                json={"name": "BrandNew", "leader": "Alice"},
            )

            # GET /current returns the latest (m_new) — it should have the project.
            r = client.get("/internal/meeting/current")
            assert [p["name"] for p in r.json()["meeting"]["projects"]] == ["BrandNew"]

            # Direct query for the old meeting's projects via the DB:
            async def _project_names_for(session):
                rows = await session.execute(
                    select(Project.name).where(Project.meeting_instance_id == m_old)
                )
                return [name for (name,) in rows.all()]

            assert db_run(_project_names_for) == []
    finally:
        _reset()


def test_duplicate_project_name_on_same_meeting_is_rejected(make_client):
    """Defensive: same name + same meeting => 409."""
    _reset()
    try:
        meeting_id = _seed_meeting(date.today())
        with make_client(ALICE) as client:
            r1 = client.post(
                f"/internal/meeting/{meeting_id}/projects",
                json={"name": "CETIN", "leader": "Jachym"},
            )
            assert r1.status_code == 200
            r2 = client.post(
                f"/internal/meeting/{meeting_id}/projects",
                json={"name": "CETIN", "leader": "Jachym"},
            )
            assert r2.status_code == 409
    finally:
        _reset()


def test_missing_fields_rejected(make_client):
    """Validation: name and leader are required."""
    _reset()
    try:
        meeting_id = _seed_meeting(date.today())
        with make_client(ALICE) as client:
            r = client.post(
                f"/internal/meeting/{meeting_id}/projects",
                json={"name": "", "leader": "Foo"},
            )
            assert r.status_code == 422
            r = client.post(
                f"/internal/meeting/{meeting_id}/projects",
                json={"name": "X"},
            )
            assert r.status_code == 422
    finally:
        _reset()
