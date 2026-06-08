"""Demo list endpoints (REQ-046).

- GET /meeting/{id}/demos returns a flat ordered list of every demo, with
  project info and the presenter's display_name.
- PUT /meeting/{id}/demos/order (admin-only) persists the reading order.
"""
from datetime import date, datetime, timezone

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
CAROL = "carol@test.example"


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


def _seed():
    """Two projects, three resolved Persons (Alice/Bob/Carol), one meeting."""
    async def _do(session):
        m = MeetingInstance(meeting_date=date(2026, 6, 1))
        now = datetime.now(timezone.utc)
        alice = Person(display_name="Alice Person", email=ALICE, first_seen_at=now)
        bob = Person(display_name="Bob Person", email=BOB, first_seen_at=now)
        carol = Person(display_name="Carol Person", email=CAROL, first_seen_at=now)
        leader = Person(display_name="Lead", email=None)
        session.add_all([m, alice, bob, carol, leader])
        await session.flush()
        p1 = Project(
            name="CETIN", leader_person_id=leader.id, created_by_email=ALICE,
        )
        p2 = Project(
            name="Medin", leader_person_id=leader.id, created_by_email=ALICE,
        )
        session.add_all([p1, p2])
        await session.commit()
        await session.refresh(m)
        await session.refresh(p1)
        await session.refresh(p2)
        return m.id, p1.id, p2.id

    return db_run(_do)


def _register(client: TestClient, email: str, meeting_id: int, project_id: int, text: str):
    """Helper that uses /my-entry to register a demo for `email`. The
    fixture rewires require_auth per call via the make_client factory."""
    r = client.put(
        f"/internal/meeting/{meeting_id}/my-entry",
        json={"project_entries": [{"project_id": project_id, "description": text}]},
    )
    assert r.status_code == 200, r.text


def test_demos_endpoint_returns_flat_ordered_list(make_client):
    """GET /demos returns id, project, presenter_display_name, description."""
    _reset()
    try:
        meeting_id, p1, p2 = _seed()
        with make_client(BOB) as bob:
            _register(bob, BOB, meeting_id, p1, "Bob on CETIN")
        with make_client(CAROL) as carol:
            _register(carol, CAROL, meeting_id, p2, "Carol on Medin")
        with make_client(ALICE) as alice:
            r = alice.get(f"/internal/meeting/{meeting_id}/demos")
        assert r.status_code == 200, r.text
        demos = r.json()["demos"]
        assert len(demos) == 2
        # New demos get appended (order_index 0, then 1) — Bob first, Carol second.
        assert demos[0]["user_email"] == BOB
        assert demos[0]["project"]["name"] == "CETIN"
        assert demos[0]["description"] == "Bob on CETIN"
        assert demos[0]["presenter_display_name"] == "Bob Person"
        assert demos[1]["user_email"] == CAROL
        assert demos[1]["description"] == "Carol on Medin"
        assert demos[0]["order_index"] < demos[1]["order_index"]
        # Each row has a stable id we can reorder by.
        assert isinstance(demos[0]["id"], int)
    finally:
        _reset()


def test_reorder_persists_and_admin_only(make_client):
    """PUT /demos/order: admin reorders; non-admin gets 403."""
    _reset()
    try:
        meeting_id, p1, p2 = _seed()
        with make_client(BOB) as bob:
            _register(bob, BOB, meeting_id, p1, "B1")
        with make_client(CAROL) as carol:
            _register(carol, CAROL, meeting_id, p2, "C1")

        with make_client(ALICE, admin=True) as admin:
            demos = admin.get(f"/internal/meeting/{meeting_id}/demos").json()["demos"]
            ids = [d["id"] for d in demos]
            # Reverse the order.
            r = admin.put(
                f"/internal/meeting/{meeting_id}/demos/order",
                json={"order": list(reversed(ids))},
            )
            assert r.status_code == 200, r.text

            # GET reflects the new order.
            after = admin.get(f"/internal/meeting/{meeting_id}/demos").json()["demos"]
            assert [d["id"] for d in after] == list(reversed(ids))
            assert after[0]["order_index"] == 0
            assert after[1]["order_index"] == 1

        with make_client(BOB, admin=False) as bob:
            r = bob.put(
                f"/internal/meeting/{meeting_id}/demos/order",
                json={"order": ids},
            )
            assert r.status_code == 403, r.text
    finally:
        _reset()


def test_reorder_rejects_unknown_ids(make_client):
    """Unknown demo id (or id from another meeting) → 422."""
    _reset()
    try:
        meeting_id, p1, _ = _seed()
        with make_client(BOB) as bob:
            _register(bob, BOB, meeting_id, p1, "B1")
        with make_client(ALICE, admin=True) as admin:
            r = admin.put(
                f"/internal/meeting/{meeting_id}/demos/order",
                json={"order": [999999]},
            )
            assert r.status_code == 422
    finally:
        _reset()


def test_reorder_partial_list_pushes_unlisted_to_end(make_client):
    """If the request only lists some demos, the unlisted ones move to
    the end (preserving their relative order). This makes the endpoint
    robust to a new demo appearing between the host's fetch and reorder."""
    _reset()
    try:
        meeting_id, p1, p2 = _seed()
        with make_client(BOB) as bob:
            _register(bob, BOB, meeting_id, p1, "B1")
        with make_client(CAROL) as carol:
            _register(carol, CAROL, meeting_id, p2, "C1")

        with make_client(ALICE, admin=True) as admin:
            demos = admin.get(f"/internal/meeting/{meeting_id}/demos").json()["demos"]
            # Two demos exist; reorder only one of them (Carol's). Bob's
            # demo isn't in the list; it must end up after Carol's.
            carol_id = next(d["id"] for d in demos if d["user_email"] == CAROL)
            r = admin.put(
                f"/internal/meeting/{meeting_id}/demos/order",
                json={"order": [carol_id]},
            )
            assert r.status_code == 200

            after = admin.get(f"/internal/meeting/{meeting_id}/demos").json()["demos"]
            assert after[0]["user_email"] == CAROL
            assert after[1]["user_email"] == BOB
    finally:
        _reset()


def test_new_demos_get_appended_to_end(make_client):
    """After a reorder, a freshly-registered demo lands at the end of the
    reading order, not at index 0 (which would otherwise collide)."""
    _reset()
    try:
        meeting_id, p1, p2 = _seed()
        with make_client(BOB) as bob:
            _register(bob, BOB, meeting_id, p1, "B1")
        with make_client(CAROL) as carol:
            _register(carol, CAROL, meeting_id, p2, "C1")

        with make_client(ALICE, admin=True) as admin:
            demos = admin.get(f"/internal/meeting/{meeting_id}/demos").json()["demos"]
            ids = [d["id"] for d in demos]
            admin.put(
                f"/internal/meeting/{meeting_id}/demos/order",
                json={"order": list(reversed(ids))},
            )

        # Alice now registers a demo (a fresh ProjectEntry).
        with make_client(ALICE, admin=False) as alice:
            _register(alice, ALICE, meeting_id, p1, "Alice late entry")
            after = alice.get(f"/internal/meeting/{meeting_id}/demos").json()["demos"]
            # Alice should be last (appended), not first.
            assert after[-1]["user_email"] == ALICE
            assert after[-1]["description"] == "Alice late entry"
    finally:
        _reset()
