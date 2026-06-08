"""GET /internal/meeting/{id}/attendees three-state roster.

Three statuses matter to the UI:
- `yes`         — submitted an entry with attending=True
- `no`          — submitted an entry with attending=False
- `no_response` — never opened the form for this meeting

The third state separates "explicitly skipping" from "hasn't replied
yet" — they look identical to the old boolean shape and conflating
them hides the people who still owe an answer.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import record_login, require_auth
from app.main import app
from app.models import (
    MeetingEntry,
    MeetingInstance,
    MeetingSchedule,
    Person,
    Project,
    ProjectEntry,
    ProjectSubscription,
)
from tests.conftest import clear_table, db_run


ALICE_EMAIL = "alice@test.example"
ALICE_NAME = "Alice Anderson"
BOB_EMAIL = "bob@test.example"
BOB_NAME = "Bob Brown"
CAROL_EMAIL = "carol@test.example"
CAROL_NAME = "Carol Clarke"


def _reset():
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(Person)
    clear_table(MeetingSchedule)


def _register(email: str, name: str):
    async def _do(session):
        await record_login(email, session, display_name=name)
    db_run(_do)


def _seed_meeting() -> int:
    async def _do(session):
        m = MeetingInstance(meeting_date=date.today() + timedelta(days=7))
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m.id
    return db_run(_do)


def _seed_entry(meeting_id: int, email: str, attending: bool):
    async def _do(session):
        session.add(MeetingEntry(
            meeting_instance_id=meeting_id,
            user_email=email,
            attending=attending,
        ))
        await session.commit()
    db_run(_do)


@pytest.fixture
def client():
    async def _override(request: Request):
        request.state.claims = {
            "preferred_username": "alice",
            "email": ALICE_EMAIL,
            "group_membership": ["/NikitaPlay"],
        }

    app.dependency_overrides[require_auth] = _override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_attendees_includes_display_name_and_three_states(client):
    _reset()
    try:
        # Three resolved Persons; one says yes, one says no, one hasn't responded.
        _register(ALICE_EMAIL, ALICE_NAME)
        _register(BOB_EMAIL, BOB_NAME)
        _register(CAROL_EMAIL, CAROL_NAME)
        meeting_id = _seed_meeting()
        _seed_entry(meeting_id, ALICE_EMAIL, attending=True)
        _seed_entry(meeting_id, BOB_EMAIL, attending=False)
        # Carol intentionally left out.

        r = client.get(f"/internal/meeting/{meeting_id}/attendees")
        assert r.status_code == 200
        by_email = {a["email"]: a for a in r.json()["attendees"]}

        assert by_email[ALICE_EMAIL]["status"] == "yes"
        assert by_email[ALICE_EMAIL]["display_name"] == ALICE_NAME
        assert by_email[BOB_EMAIL]["status"] == "no"
        assert by_email[BOB_EMAIL]["display_name"] == BOB_NAME
        assert by_email[CAROL_EMAIL]["status"] == "no_response"
        assert by_email[CAROL_EMAIL]["display_name"] == CAROL_NAME
    finally:
        _reset()


def test_attendees_excludes_placeholders(client):
    """Placeholders (no email) can't submit attendance, so they have no
    business showing up in the roster."""
    _reset()
    try:
        _register(ALICE_EMAIL, ALICE_NAME)
        # Placeholder created by admin.
        async def _add_placeholder(session):
            session.add(Person(display_name="Unlinked Person", email=None))
            await session.commit()
        db_run(_add_placeholder)

        meeting_id = _seed_meeting()
        r = client.get(f"/internal/meeting/{meeting_id}/attendees")
        emails = [a["email"] for a in r.json()["attendees"]]
        assert ALICE_EMAIL in emails
        assert all("@" in e for e in emails)
        assert len(emails) == 1
    finally:
        _reset()


def test_attendees_excludes_persons_who_logged_in_after_meeting(client):
    """Someone who first appears the week after shouldn't appear on a
    past meeting's roster."""
    _reset()
    try:
        # Alice is here from the start.
        _register(ALICE_EMAIL, ALICE_NAME)
        meeting_id = _seed_meeting()

        # Bob appears AFTER the meeting day.
        _register(BOB_EMAIL, BOB_NAME)
        future = datetime.now(timezone.utc) + timedelta(days=14)
        async def _push_bob_forward(session):
            from sqlalchemy import update
            await session.execute(
                update(Person).where(Person.email == BOB_EMAIL).values(first_seen_at=future)
            )
            await session.commit()
        db_run(_push_bob_forward)

        r = client.get(f"/internal/meeting/{meeting_id}/attendees")
        emails = [a["email"] for a in r.json()["attendees"]]
        assert ALICE_EMAIL in emails
        assert BOB_EMAIL not in emails
    finally:
        _reset()
