"""Person table + OIDC login pair-up logic.

Covers:
- Login of a brand-new email creates a fresh Person.
- Login with the same email is idempotent (first_seen_at stable).
- Login with a display_name matching an existing PLACEHOLDER promotes
  it: email is set, first_seen_at populated, the same Person.id is
  reused (so Project.leader_person_id still points at the right row).
- Two placeholders with the same display_name → only the oldest is
  promoted; the rest stay placeholders.
- The attendee endpoint only includes resolved Persons (email NOT NULL).
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
    Person,
    Project,
    ProjectSubscription,
    MeetingSchedule,
)
from tests.conftest import clear_table, db_run, make_person


ALICE_EMAIL = "alice@test.example"
ALICE_NAME = "Alice Anderson"
BOB_EMAIL = "bob@test.example"
BOB_NAME = "Bob Brown"


def _reset():
    clear_table(ProjectEntry)
    clear_table(ProjectSubscription)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(Person)
    clear_table(MeetingSchedule)


def _all_persons():
    async def _do(session):
        rows = (
            await session.execute(
                select(Person).order_by(Person.id)
            )
        ).scalars().all()
        return [
            {
                "id": p.id,
                "display_name": p.display_name,
                "email": p.email,
                "first_seen_at": p.first_seen_at,
            }
            for p in rows
        ]

    return db_run(_do)


def _record(email: str, name: str | None = None):
    async def _do(session):
        await record_login(email, session, display_name=name)

    db_run(_do)


def _backdate(person_id: int, when: datetime):
    async def _do(session):
        await session.execute(
            update(Person).where(Person.id == person_id).values(first_seen_at=when)
        )
        await session.commit()

    db_run(_do)


@pytest.fixture
def client_as_alice():
    async def _override(request: Request):
        request.state.claims = {
            "preferred_username": "alice",
            "email": ALICE_EMAIL,
            "group_membership": ["/NikitaPlay", "/NikitaPlay/admin"],
        }

    app.dependency_overrides[require_auth] = _override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_fresh_login_creates_person():
    _reset()
    try:
        _record(ALICE_EMAIL, ALICE_NAME)
        ps = _all_persons()
        assert len(ps) == 1
        assert ps[0]["email"] == ALICE_EMAIL
        assert ps[0]["display_name"] == ALICE_NAME
        assert ps[0]["first_seen_at"] is not None
    finally:
        _reset()


def test_repeated_logins_keep_first_seen_at_stable():
    _reset()
    try:
        _record(ALICE_EMAIL, ALICE_NAME)
        original = _all_persons()[0]["first_seen_at"]
        for _ in range(5):
            _record(ALICE_EMAIL, ALICE_NAME)
        rows = _all_persons()
        assert len(rows) == 1
        assert rows[0]["first_seen_at"] == original
    finally:
        _reset()


def test_login_pairs_placeholder_by_display_name():
    """The headline behavior: admin creates a placeholder, that user
    later signs in, the placeholder is promoted in place (same id)."""
    _reset()
    try:
        placeholder_id = make_person(ALICE_NAME, email=None)
        _record(ALICE_EMAIL, ALICE_NAME)

        ps = _all_persons()
        assert len(ps) == 1, "no extra Person should have been created"
        assert ps[0]["id"] == placeholder_id, "same id is reused"
        assert ps[0]["email"] == ALICE_EMAIL
        assert ps[0]["first_seen_at"] is not None
    finally:
        _reset()


def test_only_oldest_of_duplicate_placeholders_is_paired():
    _reset()
    try:
        first_id = make_person(ALICE_NAME, email=None)
        second_id = make_person(ALICE_NAME, email=None)
        # Make `first` strictly older.
        async def _backdate_created(session):
            from sqlalchemy import update as upd
            await session.execute(
                upd(Person).where(Person.id == first_id).values(
                    first_seen_at=None,
                )
            )
            await session.commit()

        db_run(_backdate_created)

        _record(ALICE_EMAIL, ALICE_NAME)

        ps = {p["id"]: p for p in _all_persons()}
        assert ps[first_id]["email"] == ALICE_EMAIL
        assert ps[second_id]["email"] is None, "second placeholder stays unpaired"
    finally:
        _reset()


def test_login_with_no_matching_placeholder_creates_new():
    _reset()
    try:
        make_person("Someone Else", email=None)
        _record(ALICE_EMAIL, ALICE_NAME)
        ps = _all_persons()
        assert len(ps) == 2
        emails = {p["email"] for p in ps}
        assert emails == {None, ALICE_EMAIL}
    finally:
        _reset()


def test_login_heals_email_fallback_with_claim_name():
    """First login had no usable claim → display_name=email. Once the
    realm starts returning a real name, the next login adopts it."""
    _reset()
    try:
        _record(ALICE_EMAIL, ALICE_EMAIL)  # email-fallback state
        _record(ALICE_EMAIL, ALICE_NAME)
        ps = _all_persons()
        assert len(ps) == 1
        assert ps[0]["display_name"] == ALICE_NAME
    finally:
        _reset()


def test_login_does_not_overwrite_curated_display_name():
    """Once display_name diverges from email — either via an earlier
    real-name claim or an admin rename — claims must not stomp it on
    subsequent logins, or admin curation would be pointless."""
    _reset()
    try:
        _record(ALICE_EMAIL, "Curated Name")
        _record(ALICE_EMAIL, "Some Other Claim Name")
        ps = _all_persons()
        assert len(ps) == 1
        assert ps[0]["display_name"] == "Curated Name"
    finally:
        _reset()


def test_attendee_list_excludes_placeholders(client_as_alice):
    """Placeholders (no email) are not real attendees and must not
    appear in the meeting attendee endpoint."""
    _reset()
    try:
        # Setup: one resolved person + one placeholder.
        _record(ALICE_EMAIL, ALICE_NAME)
        make_person("Phantom Person", email=None)

        async def _seed_meeting(session):
            m = MeetingInstance(meeting_date=date.today() + timedelta(days=7))
            session.add(m)
            await session.commit()
            await session.refresh(m)
            return m.id

        meeting_id = db_run(_seed_meeting)

        r = client_as_alice.get(f"/internal/meeting/{meeting_id}/attendees")
        emails = [a["email"] for a in r.json()["attendees"]]
        assert ALICE_EMAIL in emails
        # No placeholder display_name should leak into the attendee list.
        assert all("@" in e for e in emails)
    finally:
        _reset()
