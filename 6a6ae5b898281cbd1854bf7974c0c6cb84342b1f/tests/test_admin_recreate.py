"""Admin-only manual create/recreate of a Demo meeting (testing helper)."""
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


def _reset():
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(Person)
    clear_table(MeetingSchedule)


def _make_override(email: str, admin: bool):
    groups = ["/NikitaPlay"] + (["/NikitaPlay/admin"] if admin else [])

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


def test_admin_recreate_creates_when_missing(make_client):
    _reset()
    try:
        with make_client(ALICE, admin=True) as admin:
            r = admin.post(
                "/internal/admin/meeting/recreate",
                json={"date": "2026-06-22"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["meeting_date"] == "2026-06-22"

        async def _count(session):
            rows = await session.execute(
                select(MeetingInstance).where(MeetingInstance.meeting_date == date(2026, 6, 22))
            )
            return len(list(rows.scalars().all()))

        assert db_run(_count) == 1
    finally:
        _reset()


def test_admin_recreate_wipes_existing_meeting_and_entries(make_client):
    _reset()
    try:
        # Pre-seed a meeting with a project + entry that should be wiped.
        async def _seed(session):
            m = MeetingInstance(meeting_date=date(2026, 6, 22))
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
            await session.flush()
            entry = MeetingEntry(
                meeting_instance_id=m.id, user_email=ALICE, attending=True
            )
            session.add(entry)
            await session.flush()
            session.add(
                ProjectEntry(
                    meeting_entry_id=entry.id, project_id=p.id, description="old"
                )
            )
            await session.commit()
            return m.id

        old_id = db_run(_seed)

        with make_client(ALICE, admin=True) as admin:
            r = admin.post(
                "/internal/admin/meeting/recreate",
                json={"date": "2026-06-22"},
            )
            assert r.status_code == 200
            new_id = r.json()["id"]
            assert new_id != old_id

        async def _children_gone(session):
            # MeetingEntry + (via cascade) ProjectEntry rows tied to the
            # old meeting must be gone. Projects are global now, so they
            # survive the meeting reset (catalog persistence is the whole
            # point of REQ-009).
            rows = await session.execute(
                select(MeetingEntry).where(MeetingEntry.meeting_instance_id == old_id)
            )
            assert list(rows.scalars().all()) == []
            rows = await session.execute(
                select(Project).where(Project.name == "CETIN")
            )
            assert len(list(rows.scalars().all())) == 1, "global project survives"

        db_run(_children_gone)
    finally:
        _reset()


def test_non_admin_recreate_rejected(make_client):
    _reset()
    try:
        with make_client(ALICE, admin=False) as non_admin:
            r = non_admin.post(
                "/internal/admin/meeting/recreate",
                json={"date": "2026-06-22"},
            )
            assert r.status_code == 403
    finally:
        _reset()


def test_admin_recreate_defaults_to_today(make_client):
    _reset()
    try:
        with make_client(ALICE, admin=True) as admin:
            r = admin.post("/internal/admin/meeting/recreate", json={})
            assert r.status_code == 200
            # We don't pin today's value here (timezone-sensitive), just
            # check the response is a valid ISO date.
            date.fromisoformat(r.json()["meeting_date"])
    finally:
        _reset()


def test_admin_recreate_validates_date_format(make_client):
    _reset()
    try:
        with make_client(ALICE, admin=True) as admin:
            r = admin.post(
                "/internal/admin/meeting/recreate",
                json={"date": "not-a-date"},
            )
            assert r.status_code == 422
    finally:
        _reset()
