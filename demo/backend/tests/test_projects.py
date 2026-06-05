"""REQ-009 (renamed REQ-006): global project catalog + subscriptions.

Covers:
- AI-025 (rewritten): any signed-in user can POST a new global project.
- AI-026 (rewritten): a project added by one user is visible to other
  users via /projects.
- AI-027 (rewritten): projects persist between meetings — they are NOT
  scoped to the meeting they were created from. (Old AI-027 retired.)
- Subscriptions: list / explicit subscribe / explicit unsubscribe.
- Auto-subscribe when a user writes a note for an unsubscribed project.
- Edit (rename, change leader) — anyone signed in.
- Delete — anyone signed in (cascades to entries + subscriptions).
- Search via ?q=.
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


def _seed_meeting(meeting_date: date) -> int:
    async def _do(session):
        m = MeetingInstance(meeting_date=meeting_date)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m.id

    return db_run(_do)


# --- Catalog CRUD ---


def test_anyone_can_create_a_project(make_client):
    """AI-025 (rewritten): any signed-in user can POST /projects."""
    _reset()
    try:
        with make_client(ALICE) as alice:
            r = alice.post("/internal/projects", json={"name": "CETIN", "leader": "Jachym"})
            assert r.status_code == 200, r.text
            assert r.json()["name"] == "CETIN"
            assert r.json()["leader"] == "Jachym"
    finally:
        _reset()


def test_created_project_visible_to_other_users(make_client):
    """AI-026 (rewritten): visible via GET /projects."""
    _reset()
    try:
        with make_client(ALICE) as alice:
            alice.post("/internal/projects", json={"name": "Medin", "leader": "Tim"})
        with make_client(BOB) as bob:
            r = bob.get("/internal/projects")
            names = [p["name"] for p in r.json()["projects"]]
            assert "Medin" in names
    finally:
        _reset()


def test_projects_persist_between_meetings(make_client):
    """AI-027 (rewritten): a project created in the context of one
    meeting still appears (and is selectable) when a new meeting is
    created later — global catalog, no per-meeting scoping."""
    _reset()
    try:
        with make_client(ALICE) as alice:
            r = alice.post("/internal/projects", json={"name": "CETIN", "leader": "Jachym"})
            project_id = r.json()["id"]
        m1 = _seed_meeting(date(2026, 6, 1))
        m2 = _seed_meeting(date(2026, 6, 8))
        with make_client(BOB) as bob:
            # Bob can write a CETIN entry on BOTH meetings — same project_id.
            for m_id in (m1, m2):
                r = bob.put(
                    f"/internal/meeting/{m_id}/my-entry",
                    json={
                        "attending": True,
                        "project_entries": [{"project_id": project_id, "description": "x"}],
                    },
                )
                assert r.status_code == 200, r.text
    finally:
        _reset()


def test_duplicate_name_rejected(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            r1 = alice.post("/internal/projects", json={"name": "CETIN", "leader": "Jachym"})
            assert r1.status_code == 200
            r2 = alice.post("/internal/projects", json={"name": "CETIN", "leader": "Other"})
            assert r2.status_code == 409
    finally:
        _reset()


def test_search_by_name_or_leader(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            for name, leader in [
                ("CETIN", "Jachym Doležal"),
                ("Medin", "Timothy Hobbs"),
                ("Avant", "Jan Kotrč"),
            ]:
                alice.post("/internal/projects", json={"name": name, "leader": leader})

            r = alice.get("/internal/projects?q=med")
            names = [p["name"] for p in r.json()["projects"]]
            assert names == ["Medin"]

            r = alice.get("/internal/projects?q=tim")
            names = [p["name"] for p in r.json()["projects"]]
            assert names == ["Medin"]  # matched via leader
    finally:
        _reset()


def test_anyone_can_edit_a_project(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            pid = alice.post(
                "/internal/projects", json={"name": "X", "leader": "Y"}
            ).json()["id"]
        with make_client(BOB) as bob:
            r = bob.put(
                f"/internal/projects/{pid}",
                json={"name": "X-renamed", "leader": "Z"},
            )
            assert r.status_code == 200
            assert r.json() == {"id": pid, "name": "X-renamed", "leader": "Z"}
    finally:
        _reset()


def test_anyone_can_delete_a_project_cascading_entries(make_client):
    _reset()
    try:
        meeting_id = _seed_meeting(date(2026, 6, 1))
        with make_client(ALICE) as alice:
            pid = alice.post(
                "/internal/projects", json={"name": "X", "leader": "Y"}
            ).json()["id"]
            alice.put(
                f"/internal/meeting/{meeting_id}/my-entry",
                json={
                    "attending": True,
                    "project_entries": [{"project_id": pid, "description": "note"}],
                },
            )
        with make_client(BOB) as bob:
            r = bob.delete(f"/internal/projects/{pid}")
            assert r.status_code == 200

        async def _gone(session):
            row = (
                await session.execute(
                    select(Project).where(Project.id == pid)
                )
            ).scalar_one_or_none()
            assert row is None
            # Entries for that project are gone (CASCADE).
            count = len(
                (
                    await session.execute(
                        select(ProjectEntry).where(ProjectEntry.project_id == pid)
                    )
                ).scalars().all()
            )
            assert count == 0

        db_run(_gone)
    finally:
        _reset()


# --- Subscriptions ---


def test_explicit_subscribe_and_unsubscribe(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            pid = alice.post(
                "/internal/projects", json={"name": "X", "leader": "Y"}
            ).json()["id"]

            assert alice.get("/internal/me/subscriptions").json() == {"subscriptions": []}

            r = alice.post(f"/internal/me/subscriptions/{pid}")
            assert r.status_code == 200
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert [s["name"] for s in subs] == ["X"]

            # Subscribing again is idempotent (still one row).
            alice.post(f"/internal/me/subscriptions/{pid}")
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert len(subs) == 1

            r = alice.delete(f"/internal/me/subscriptions/{pid}")
            assert r.status_code == 200
            assert alice.get("/internal/me/subscriptions").json() == {"subscriptions": []}
    finally:
        _reset()


def test_auto_subscribe_on_first_note(make_client):
    """The headline UX rule: writing a note for an unsubscribed project
    silently subscribes the user."""
    _reset()
    try:
        meeting_id = _seed_meeting(date(2026, 6, 1))
        with make_client(ALICE) as alice:
            pid = alice.post(
                "/internal/projects", json={"name": "X", "leader": "Y"}
            ).json()["id"]
            # Alice has not subscribed yet.
            assert alice.get("/internal/me/subscriptions").json() == {"subscriptions": []}

            # First note: should auto-subscribe.
            alice.put(
                f"/internal/meeting/{meeting_id}/my-entry",
                json={
                    "attending": True,
                    "project_entries": [{"project_id": pid, "description": "first"}],
                },
            )
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert [s["id"] for s in subs] == [pid]

            # Bob has not touched the project — Bob is NOT subscribed.
        with make_client(BOB) as bob:
            assert bob.get("/internal/me/subscriptions").json() == {"subscriptions": []}
    finally:
        _reset()


def test_subscription_isolated_per_user(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            pid = alice.post(
                "/internal/projects", json={"name": "X", "leader": "Y"}
            ).json()["id"]
            alice.post(f"/internal/me/subscriptions/{pid}")
        with make_client(BOB) as bob:
            assert bob.get("/internal/me/subscriptions").json() == {"subscriptions": []}
    finally:
        _reset()


def test_subscribe_to_missing_project_404(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            r = alice.post("/internal/me/subscriptions/999999")
            assert r.status_code == 404
    finally:
        _reset()


def test_delete_project_cascades_subscriptions(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            pid = alice.post(
                "/internal/projects", json={"name": "X", "leader": "Y"}
            ).json()["id"]
            alice.post(f"/internal/me/subscriptions/{pid}")
            alice.delete(f"/internal/projects/{pid}")
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert subs == []
    finally:
        _reset()
