"""REQ-008: history view backend.

- AI-033: GET /internal/meetings lists meetings most-recent-first.
- AI-034: GET /internal/meeting/{id}/details returns the full meeting
  view (projects + attendees joined with each user's entry).
- AI-035 backend support: admin can update attendees from the history
  detail view via PUT /internal/meeting/{id}/entries/{user_email}
  (covered by test_permissions.py — re-asserted here against a
  historical meeting).
"""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
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


ALICE = "alice@test.example"
BOB = "bob@test.example"


def _reset():
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(UserRoster)
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


def _record(email: str):
    async def _do(session):
        await record_login(email, session)

    db_run(_do)


def _backdate_first_seen(email: str, when: datetime):
    async def _do(session):
        await session.execute(
            update(UserRoster).where(UserRoster.email == email).values(first_seen_at=when)
        )
        await session.commit()

    db_run(_do)


def _seed_meeting(meeting_date: date) -> int:
    async def _do(session):
        m = MeetingInstance(meeting_date=meeting_date)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m.id

    return db_run(_do)


def test_list_meetings_most_recent_first(make_client):
    """AI-033."""
    _reset()
    try:
        _seed_meeting(date(2026, 5, 25))
        _seed_meeting(date(2026, 6, 8))
        _seed_meeting(date(2026, 6, 1))
        with make_client(ALICE) as client:
            r = client.get("/internal/meetings")
            assert r.status_code == 200, r.text
            dates = [m["meeting_date"] for m in r.json()["meetings"]]
            assert dates == ["2026-06-08", "2026-06-01", "2026-05-25"]
    finally:
        _reset()


def test_meeting_details_returns_projects_and_attendees(make_client):
    """AI-034: detail view shows projects + roster-joined attendees."""
    _reset()
    try:
        meeting_id = _seed_meeting(date(2026, 6, 1))

        # Seed two roster entries and an attendance row for alice.
        _record(ALICE)
        _record(BOB)
        _backdate_first_seen(ALICE, datetime(2026, 5, 1, tzinfo=timezone.utc))
        _backdate_first_seen(BOB, datetime(2026, 5, 1, tzinfo=timezone.utc))

        async def _seed_extras(session):
            p = Project(
                name="CETIN",
                leader="Jachym",
                created_by_email="seed@test.example",
            )
            session.add(p)
            await session.flush()
            session.add(
                MeetingEntry(
                    meeting_instance_id=meeting_id,
                    user_email=ALICE,
                    attended=True,
                )
            )
            await session.flush()
            entry = (
                await session.execute(
                    __import__("sqlalchemy").select(MeetingEntry).where(
                        MeetingEntry.meeting_instance_id == meeting_id,
                        MeetingEntry.user_email == ALICE,
                    )
                )
            ).scalar_one()
            session.add(
                ProjectEntry(
                    meeting_entry_id=entry.id,
                    project_id=p.id,
                    description="Power consumption — PE",
                )
            )
            await session.commit()
            return p.id

        project_id = db_run(_seed_extras)

        with make_client(ALICE) as client:
            r = client.get(f"/internal/meeting/{meeting_id}/details")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["meeting"]["id"] == meeting_id
            assert [p["name"] for p in data["meeting"]["projects"]] == ["CETIN"]
            by_email = {a["email"]: a for a in data["attendees"]}
            assert by_email[ALICE]["attended"] is True
            assert by_email[ALICE]["project_entries"] == [
                {"project_id": project_id, "description": "Power consumption — PE"}
            ]
            assert by_email[BOB]["attended"] is False
            assert by_email[BOB]["project_entries"] == []
    finally:
        _reset()


def test_meeting_details_404_when_unknown(make_client):
    _reset()
    try:
        with make_client(ALICE) as client:
            r = client.get("/internal/meeting/999999/details")
            assert r.status_code == 404
    finally:
        _reset()


def test_admin_can_edit_historic_entry(make_client):
    """AI-035: admin can edit attendance + project notes on a past
    meeting via the existing admin entry endpoint."""
    _reset()
    try:
        meeting_id = _seed_meeting(date(2026, 5, 25))  # historic
        _record(BOB)
        _backdate_first_seen(BOB, datetime(2026, 5, 1, tzinfo=timezone.utc))

        async def _seed_p(session):
            p = Project(
                name="X",
                leader="Y",
                created_by_email="seed@test.example",
            )
            session.add(p)
            await session.commit()
            await session.refresh(p)
            return p.id

        project_id = db_run(_seed_p)

        with make_client(ALICE, admin=True) as admin:
            r = admin.put(
                f"/internal/meeting/{meeting_id}/entries/{BOB}",
                json={
                    "attended": True,
                    "project_entries": [
                        {"project_id": project_id, "description": "Backfilled by admin"},
                    ],
                },
            )
            assert r.status_code == 200, r.text

            # Confirm via /details.
            r = admin.get(f"/internal/meeting/{meeting_id}/details")
            by_email = {a["email"]: a for a in r.json()["attendees"]}
            assert by_email[BOB]["attended"] is True
            assert by_email[BOB]["project_entries"] == [
                {"project_id": project_id, "description": "Backfilled by admin"},
            ]
    finally:
        _reset()
