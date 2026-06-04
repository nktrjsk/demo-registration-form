"""REQ-005: roster auto-grow from OIDC logins.

- AI-022: first OIDC login → user appears on current + future meetings
  (but not retroactively on past meetings).
- AI-023: logging in N times leaves exactly one roster row.
- AI-024: a user who has never authenticated does NOT appear in the
  roster (no admin add-by-email escape hatch).
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from starlette.requests import Request

from app.auth import record_login, require_auth
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
NEVER = "never-logged-in@test.example"


def _reset():
    clear_table(ProjectEntry)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(UserRoster)


def _record(email: str):
    async def _do(session):
        await record_login(email, session)

    db_run(_do)


def _backdate_first_seen(email: str, when: datetime):
    """Move a roster row's first_seen_at to a specific time. Lets us
    simulate users having logged in on different days."""
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


def _roster_emails():
    async def _do(session):
        rows = (await session.execute(select(UserRoster.email).order_by(UserRoster.email))).all()
        return [row[0] for row in rows]

    return db_run(_do)


@pytest.fixture
def client():
    """Permissive client whose claims include any group; tests can call
    /meeting/{id}/attendees without 401/403 fuss."""

    async def _override(request: Request):
        request.state.claims = {
            "preferred_username": "tester",
            "email": "tester@test.example",
            "group_membership": ["/NikitaPlay", "/NikitaPlay/admin"],
        }

    app.dependency_overrides[require_auth] = _override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_first_login_lands_in_roster():
    """AI-022 / AI-023: a fresh login adds exactly one row."""
    _reset()
    try:
        _record(ALICE)
        assert _roster_emails() == [ALICE]
    finally:
        _reset()


def test_repeated_logins_do_not_duplicate():
    """AI-023: idempotent — re-logging in does not create a second row."""
    _reset()
    try:
        for _ in range(5):
            _record(ALICE)
        assert _roster_emails() == [ALICE]
    finally:
        _reset()


def test_user_appears_on_current_and_future_not_past(client):
    """AI-022: a user who first logs in on day X appears on meetings
    dated X and later, but NOT on past meetings."""
    _reset()
    try:
        past = _seed_meeting(date(2026, 5, 25))
        current = _seed_meeting(date(2026, 6, 1))
        future = _seed_meeting(date(2026, 6, 8))

        # Alice's first login is on 2026-06-01 (the current meeting's date).
        _record(ALICE)
        _backdate_first_seen(ALICE, datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))

        past_emails = [a["email"] for a in client.get(f"/internal/meeting/{past}/attendees").json()["attendees"]]
        current_emails = [a["email"] for a in client.get(f"/internal/meeting/{current}/attendees").json()["attendees"]]
        future_emails = [a["email"] for a in client.get(f"/internal/meeting/{future}/attendees").json()["attendees"]]

        assert ALICE not in past_emails
        assert ALICE in current_emails
        assert ALICE in future_emails
    finally:
        _reset()


def test_user_never_logged_in_is_not_in_roster(client):
    """AI-024: never-authenticated users do NOT appear in the roster or
    attendee lists."""
    _reset()
    try:
        # Bob logs in. NEVER doesn't.
        _record(BOB)
        future = _seed_meeting(date.today() + timedelta(days=30))

        emails = [a["email"] for a in client.get(f"/internal/meeting/{future}/attendees").json()["attendees"]]
        assert BOB in emails
        assert NEVER not in emails
        assert _roster_emails() == [BOB]
    finally:
        _reset()


def test_attendees_includes_attendance_flag(client):
    """End-to-end: roster entry + meeting entry surface via /attendees."""
    _reset()
    try:
        meeting_id = _seed_meeting(date.today() + timedelta(days=7))
        _record(ALICE)
        _record(BOB)

        async def _set_entry(session):
            session.add(
                MeetingEntry(
                    meeting_instance_id=meeting_id,
                    user_email=ALICE,
                    attended=True,
                )
            )
            await session.commit()

        db_run(_set_entry)

        attendees = client.get(f"/internal/meeting/{meeting_id}/attendees").json()["attendees"]
        by_email = {a["email"]: a["attended"] for a in attendees}
        assert by_email[ALICE] is True
        assert by_email[BOB] is False
    finally:
        _reset()
