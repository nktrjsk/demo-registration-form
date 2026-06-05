"""Global project catalog + subscriptions, using Person FK for leader.

Covers:
- AI-025 / 026 / 027 (rewritten): any user can POST a new global project;
  it's visible to other users; projects persist across meetings.
- Subscriptions: list / explicit subscribe / explicit unsubscribe.
- Auto-subscribe when a user writes a note for an unsubscribed project.
- Edit (rename, change leader_person_id) — anyone signed in.
- Delete — anyone signed in (cascades to entries + subscriptions).
- Search via ?q= matches project name, leader display_name, leader email.
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


def _new_leader(client: TestClient, name: str) -> int:
    """Test helper: create a placeholder Person and return its id."""
    return client.post(
        "/internal/people", json={"display_name": name}
    ).json()["id"]


def _new_project(client: TestClient, name: str, leader_name: str) -> dict:
    leader_id = _new_leader(client, leader_name)
    r = client.post(
        "/internal/projects",
        json={"name": name, "leader_person_id": leader_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- Catalog CRUD ---


def test_anyone_can_create_a_project(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            project = _new_project(alice, "CETIN", "Jachym Doležal")
            assert project["name"] == "CETIN"
            assert project["leader"]["display_name"] == "Jachym Doležal"
            assert project["leader"]["email"] is None
            assert project["leader"]["resolved"] is False
    finally:
        _reset()


def test_created_project_visible_to_other_users(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            _new_project(alice, "Medin", "Timothy Hobbs")
        with make_client(BOB) as bob:
            r = bob.get("/internal/projects")
            names = [p["name"] for p in r.json()["projects"]]
            assert "Medin" in names
    finally:
        _reset()


def test_projects_persist_between_meetings(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            project_id = _new_project(alice, "CETIN", "Jachym")["id"]
        m1 = _seed_meeting(date(2026, 6, 1))
        m2 = _seed_meeting(date(2026, 6, 8))
        with make_client(BOB) as bob:
            for m_id in (m1, m2):
                r = bob.put(
                    f"/internal/meeting/{m_id}/my-entry",
                    json={
                        "attending": True,
                        "project_entries": [
                            {"project_id": project_id, "description": "x"}
                        ],
                    },
                )
                assert r.status_code == 200, r.text
    finally:
        _reset()


def test_duplicate_name_rejected(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            _new_project(alice, "CETIN", "Jachym")
            # Re-create with a different leader still collides on name.
            leader_id = _new_leader(alice, "Other")
            r2 = alice.post(
                "/internal/projects",
                json={"name": "CETIN", "leader_person_id": leader_id},
            )
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
                _new_project(alice, name, leader)

            r = alice.get("/internal/projects?q=med")
            names = [p["name"] for p in r.json()["projects"]]
            assert names == ["Medin"]

            r = alice.get("/internal/projects?q=Tim")
            names = [p["name"] for p in r.json()["projects"]]
            assert names == ["Medin"]  # matched via leader display_name
    finally:
        _reset()


def test_anyone_can_edit_a_project(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            pid = _new_project(alice, "X", "Y")["id"]
        with make_client(BOB) as bob:
            new_leader = _new_leader(bob, "Z")
            r = bob.put(
                f"/internal/projects/{pid}",
                json={"name": "X-renamed", "leader_person_id": new_leader},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["id"] == pid
            assert body["name"] == "X-renamed"
            assert body["leader"]["display_name"] == "Z"
    finally:
        _reset()


def test_anyone_can_delete_a_project_cascading_entries(make_client):
    _reset()
    try:
        meeting_id = _seed_meeting(date(2026, 6, 1))
        with make_client(ALICE) as alice:
            pid = _new_project(alice, "X", "Y")["id"]
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
                await session.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            assert row is None
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


def test_project_payload_requires_leader_person_id(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            r = alice.post("/internal/projects", json={"name": "X"})
            assert r.status_code == 422
            r = alice.post(
                "/internal/projects",
                json={"name": "X", "leader_person_id": 999999},
            )
            assert r.status_code == 422
    finally:
        _reset()


# --- Subscriptions ---


def test_explicit_subscribe_and_unsubscribe(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            pid = _new_project(alice, "X", "Y")["id"]

            assert alice.get("/internal/me/subscriptions").json() == {"subscriptions": []}

            r = alice.post(f"/internal/me/subscriptions/{pid}")
            assert r.status_code == 200
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert [s["name"] for s in subs] == ["X"]

            alice.post(f"/internal/me/subscriptions/{pid}")
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert len(subs) == 1

            r = alice.delete(f"/internal/me/subscriptions/{pid}")
            assert r.status_code == 200
            assert alice.get("/internal/me/subscriptions").json() == {"subscriptions": []}
    finally:
        _reset()


def test_auto_subscribe_on_first_note(make_client):
    _reset()
    try:
        meeting_id = _seed_meeting(date(2026, 6, 1))
        with make_client(ALICE) as alice:
            pid = _new_project(alice, "X", "Y")["id"]
            assert alice.get("/internal/me/subscriptions").json() == {"subscriptions": []}

            alice.put(
                f"/internal/meeting/{meeting_id}/my-entry",
                json={
                    "attending": True,
                    "project_entries": [{"project_id": pid, "description": "first"}],
                },
            )
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert [s["id"] for s in subs] == [pid]
        with make_client(BOB) as bob:
            assert bob.get("/internal/me/subscriptions").json() == {"subscriptions": []}
    finally:
        _reset()


def test_subscription_isolated_per_user(make_client):
    _reset()
    try:
        with make_client(ALICE) as alice:
            pid = _new_project(alice, "X", "Y")["id"]
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
            pid = _new_project(alice, "X", "Y")["id"]
            alice.post(f"/internal/me/subscriptions/{pid}")
            alice.delete(f"/internal/projects/{pid}")
            subs = alice.get("/internal/me/subscriptions").json()["subscriptions"]
            assert subs == []
    finally:
        _reset()
