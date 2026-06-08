"""REQ-007: permissions

- AI-028: non-admin cannot edit another user's entry (403).
- AI-029: admin can edit any user's attendance (and changes persist).
- AI-030: admin can edit any user's project entries.
- AI-031: schedule + admin-only endpoints reject non-admin (overlaps
  with AI-013 for the schedule; here we cover the new admin endpoints).
"""
from datetime import date

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
    Person,
    Project,
    ProjectSubscription,
    MeetingSchedule,
)
from tests.conftest import clear_table, db_run


ALICE = "alice@test.example"
BOB = "bob@test.example"


def _reset():
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(Person)
    clear_table(MeetingSchedule)


def _make_override(email: str, admin: bool):
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


def _seed_meeting_with_project():
    async def _do(session):
        m = MeetingInstance(meeting_date=date(2026, 6, 1))
        session.add(m)
        await session.flush()
        leader = Person(display_name="Jachym", email=None)
        session.add(leader)
        await session.flush()
        p = Project(
            name="CETIN",
            leader_person_id=leader.id,
            created_by_email="seed@test.example",
        )
        session.add(p)
        await session.commit()
        await session.refresh(m)
        await session.refresh(p)
        return m.id, p.id

    return db_run(_do)


def test_non_admin_cannot_edit_others_entry(make_client):
    """AI-028: non-admin PUT to admin endpoint => 403."""
    _reset()
    try:
        meeting_id, _ = _seed_meeting_with_project()
        with make_client(ALICE, admin=False) as alice:
            r = alice.put(
                f"/internal/meeting/{meeting_id}/entries/{BOB}",
                json={"attending": True, "project_entries": []},
            )
            assert r.status_code == 403, r.text

            r = alice.get(f"/internal/meeting/{meeting_id}/entries/{BOB}")
            assert r.status_code == 403
    finally:
        _reset()


def test_admin_can_edit_other_users_attendance(make_client):
    """AI-029: admin sets Bob's attendance to True; the change persists."""
    _reset()
    try:
        meeting_id, _ = _seed_meeting_with_project()
        with make_client(ALICE, admin=True) as admin:
            r = admin.put(
                f"/internal/meeting/{meeting_id}/entries/{BOB}",
                json={"attending": True, "project_entries": []},
            )
            assert r.status_code == 200, r.text
            assert r.json()["attending"] is True

            r = admin.get(f"/internal/meeting/{meeting_id}/entries/{BOB}")
            assert r.json()["attending"] is True

        # Verify on the DB directly.
        async def _fetch(session):
            return (
                await session.execute(
                    select(MeetingEntry.attending).where(
                        MeetingEntry.meeting_instance_id == meeting_id,
                        MeetingEntry.user_email == BOB,
                    )
                )
            ).scalar_one()

        assert db_run(_fetch) is True
    finally:
        _reset()


def test_admin_can_edit_other_users_project_entries(make_client):
    """AI-030: admin sets Bob's project description; it persists."""
    _reset()
    try:
        meeting_id, project_id = _seed_meeting_with_project()
        with make_client(ALICE, admin=True) as admin:
            r = admin.put(
                f"/internal/meeting/{meeting_id}/entries/{BOB}",
                json={
                    "attending": True,
                    "project_entries": [
                        {"project_id": project_id, "description": "wrote up CETIN"}
                    ],
                },
            )
            assert r.status_code == 200, r.text
            entries = r.json()["project_entries"]
            assert entries == [{"project_id": project_id, "description": "wrote up CETIN"}]

            # GET returns the same.
            r = admin.get(f"/internal/meeting/{meeting_id}/entries/{BOB}")
            assert r.json()["project_entries"] == entries
    finally:
        _reset()


def test_admin_only_endpoints_require_admin(make_client):
    """AI-031: schedule PUT + admin entry endpoints reject non-admins."""
    _reset()
    try:
        meeting_id, _ = _seed_meeting_with_project()
        with make_client(BOB, admin=False) as non_admin:
            r = non_admin.put(
                "/internal/schedule",
                json={"weekday": 2, "start_time": "11:00"},
            )
            assert r.status_code == 403

            r = non_admin.get(f"/internal/meeting/{meeting_id}/entries/{ALICE}")
            assert r.status_code == 403

            r = non_admin.put(
                f"/internal/meeting/{meeting_id}/entries/{ALICE}",
                json={"attending": False, "project_entries": []},
            )
            assert r.status_code == 403
    finally:
        _reset()


def test_user_can_edit_own_entry(make_client):
    """Sanity: any user can edit their own /my-entry without admin role,
    so long as they don't try to mutate attendance (admin-only)."""
    _reset()
    try:
        meeting_id, project_id = _seed_meeting_with_project()
        with make_client(BOB, admin=False) as bob:
            r = bob.put(
                f"/internal/meeting/{meeting_id}/my-entry",
                json={
                    "project_entries": [
                        {"project_id": project_id, "description": "my notes"}
                    ],
                },
            )
            assert r.status_code == 200
            assert r.json()["user_email"] == BOB
            assert r.json()["project_entries"] == [
                {"project_id": project_id, "description": "my notes"}
            ]
    finally:
        _reset()


def test_non_admin_cannot_set_attending_via_my_entry(make_client):
    """REQ-007 update: attendance is admin-only. A non-admin PUT to
    /my-entry that includes `attending` is rejected with 403."""
    _reset()
    try:
        meeting_id, _ = _seed_meeting_with_project()
        with make_client(BOB, admin=False) as bob:
            r = bob.put(
                f"/internal/meeting/{meeting_id}/my-entry",
                json={"attending": True, "project_entries": []},
            )
            assert r.status_code == 403, r.text
    finally:
        _reset()


def test_admin_can_set_attending_via_own_my_entry(make_client):
    """Admins are still allowed to set their own attendance via /my-entry."""
    _reset()
    try:
        meeting_id, _ = _seed_meeting_with_project()
        with make_client(ALICE, admin=True) as admin:
            r = admin.put(
                f"/internal/meeting/{meeting_id}/my-entry",
                json={"attending": True, "project_entries": []},
            )
            assert r.status_code == 200
            assert r.json()["attending"] is True
    finally:
        _reset()


def test_admin_set_attending_persists_when_user_updates_projects(make_client):
    """End-to-end: admin marks Bob attending; Bob then writes project notes
    (without sending attending). Bob's attendance must stay True."""
    _reset()
    try:
        meeting_id, project_id = _seed_meeting_with_project()
        with make_client(ALICE, admin=True) as admin:
            r = admin.put(
                f"/internal/meeting/{meeting_id}/entries/{BOB}",
                json={"attending": True, "project_entries": []},
            )
            assert r.status_code == 200

        with make_client(BOB, admin=False) as bob:
            r = bob.put(
                f"/internal/meeting/{meeting_id}/my-entry",
                json={
                    "project_entries": [
                        {"project_id": project_id, "description": "wrote it up"}
                    ],
                },
            )
            assert r.status_code == 200
            assert r.json()["attending"] is True
    finally:
        _reset()
