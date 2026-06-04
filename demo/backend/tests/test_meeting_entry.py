"""Tests for the user meeting-form endpoints (REQ-004 / AI-017..021).

- AI-017: GET .../my-entry returns the user's email from the OIDC session.
- AI-018: attendance toggle is persisted via PUT.
- AI-019: user can select one or more projects (project_entries list).
- AI-020: descriptions are stored per project per user.
- AI-021: reopening (re-GET) returns the previously submitted values.
"""
from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from starlette.requests import Request

from app.auth import require_auth
from app.main import app
from app.models import (
    MeetingInstance,
    Project,
    MeetingEntry,
    ProjectEntry,
    MeetingSchedule,
)
from tests.conftest import clear_table, db_run


TEST_EMAIL = "alice@test.example"


async def _override_user(request: Request):
    request.state.claims = {
        "preferred_username": "alice",
        "email": TEST_EMAIL,
        "group_membership": ["/NikitaPlay"],
    }


@pytest.fixture
def user_client():
    app.dependency_overrides[require_auth] = _override_user
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _seed_meeting_with_projects():
    """Create one MeetingInstance + two Projects, return their ids."""

    async def _do(session):
        m = MeetingInstance(meeting_date=date(2026, 6, 1))
        session.add(m)
        await session.flush()
        p1 = Project(
            meeting_instance_id=m.id,
            name="CETIN",
            leader="Jachym Doležal",
            created_by_email="seed@test.example",
        )
        p2 = Project(
            meeting_instance_id=m.id,
            name="Medin",
            leader="Timothy Hobbs",
            created_by_email="seed@test.example",
        )
        session.add_all([p1, p2])
        await session.commit()
        await session.refresh(m)
        await session.refresh(p1)
        await session.refresh(p2)
        return m.id, p1.id, p2.id

    return db_run(_do)


def _reset_all():
    # ProjectEntry → MeetingEntry → Project → MeetingInstance → MeetingSchedule
    clear_table(ProjectEntry)
    clear_table(MeetingEntry)
    clear_table(Project)
    clear_table(MeetingInstance)
    clear_table(MeetingSchedule)


def test_my_entry_returns_email_from_claims(user_client):
    """AI-017: form preloads the email from OIDC, not user-supplied."""
    _reset_all()
    try:
        meeting_id, _, _ = _seed_meeting_with_projects()
        r = user_client.get(f"/internal/meeting/{meeting_id}/my-entry")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["user_email"] == TEST_EMAIL
        assert data["attended"] is False
        assert data["project_entries"] == []
    finally:
        _reset_all()


def test_current_meeting_returns_latest_with_projects(user_client):
    """The /meeting/current endpoint that the form will read on load."""
    _reset_all()
    try:
        meeting_id, p1, p2 = _seed_meeting_with_projects()
        r = user_client.get("/internal/meeting/current")
        assert r.status_code == 200
        data = r.json()
        assert data["meeting"]["id"] == meeting_id
        assert data["meeting"]["meeting_date"] == "2026-06-01"
        names = sorted(p["name"] for p in data["meeting"]["projects"])
        assert names == ["CETIN", "Medin"]
    finally:
        _reset_all()


def test_attendance_toggle_is_persisted(user_client):
    """AI-018: attendance is recorded and survives reload."""
    _reset_all()
    try:
        meeting_id, _, _ = _seed_meeting_with_projects()
        # Mark attended=True.
        r = user_client.put(
            f"/internal/meeting/{meeting_id}/my-entry",
            json={"attended": True, "project_entries": []},
        )
        assert r.status_code == 200, r.text
        assert r.json()["attended"] is True

        # GET reflects the change.
        r = user_client.get(f"/internal/meeting/{meeting_id}/my-entry")
        assert r.json()["attended"] is True

        # Toggle back to False — also persisted.
        r = user_client.put(
            f"/internal/meeting/{meeting_id}/my-entry",
            json={"attended": False, "project_entries": []},
        )
        assert r.status_code == 200
        assert r.json()["attended"] is False
    finally:
        _reset_all()


def test_select_multiple_projects_with_descriptions(user_client):
    """AI-019 + AI-020: multi-select with per-project description."""
    _reset_all()
    try:
        meeting_id, p1, p2 = _seed_meeting_with_projects()
        r = user_client.put(
            f"/internal/meeting/{meeting_id}/my-entry",
            json={
                "attended": True,
                "project_entries": [
                    {"project_id": p1, "description": "Power consumption — PE"},
                    {"project_id": p2, "description": "Medin onboarding doc"},
                ],
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["attended"] is True
        entries = {pe["project_id"]: pe["description"] for pe in data["project_entries"]}
        assert entries == {
            p1: "Power consumption — PE",
            p2: "Medin onboarding doc",
        }
    finally:
        _reset_all()


def test_reopening_returns_previously_submitted_values(user_client):
    """AI-021: submit, then GET returns the same values."""
    _reset_all()
    try:
        meeting_id, p1, _ = _seed_meeting_with_projects()
        user_client.put(
            f"/internal/meeting/{meeting_id}/my-entry",
            json={
                "attended": True,
                "project_entries": [
                    {"project_id": p1, "description": "Initial sketch"},
                ],
            },
        )
        r = user_client.get(f"/internal/meeting/{meeting_id}/my-entry")
        data = r.json()
        assert data["user_email"] == TEST_EMAIL
        assert data["attended"] is True
        assert data["project_entries"] == [
            {"project_id": p1, "description": "Initial sketch"}
        ]
    finally:
        _reset_all()


def test_put_rejects_foreign_project_id(user_client):
    """Defensive: a project_id from another meeting (or made up) is rejected."""
    _reset_all()
    try:
        meeting_id, p1, _ = _seed_meeting_with_projects()
        r = user_client.put(
            f"/internal/meeting/{meeting_id}/my-entry",
            json={
                "attended": True,
                "project_entries": [
                    {"project_id": 999999, "description": "nope"},
                ],
            },
        )
        assert r.status_code == 422
    finally:
        _reset_all()
