"""AI-012: Admin can change the demo start time; change persists in the DB."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from app.auth import require_auth
from app.main import app
from app.models import MeetingSchedule
from tests.conftest import clear_table, db_run


async def _override_admin(request: Request):
    request.state.claims = {
        "preferred_username": "test-admin",
        "email": "admin@test.example",
        "group_membership": ["/NikitaPlay", "/NikitaPlay/admin"],
    }


def _fetch_row():
    async def _do(session):
        result = await session.execute(
            select(MeetingSchedule).where(MeetingSchedule.id == 1)
        )
        return result.scalar_one_or_none()

    return db_run(_do)


@pytest.fixture
def admin_client():
    app.dependency_overrides[require_auth] = _override_admin
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_admin_can_change_start_time(admin_client):
    clear_table(MeetingSchedule)
    try:
        r = admin_client.put(
            "/internal/schedule",
            json={"weekday": 0, "start_time": "10:30"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"weekday": 0, "start_time": "10:30"}

        r = admin_client.get("/public/schedule")
        assert r.status_code == 200
        assert r.json() == {"weekday": 0, "start_time": "10:30"}

        row = _fetch_row()
        assert row is not None
        assert row.weekday == 0
        assert row.start_time.strftime("%H:%M") == "10:30"
    finally:
        clear_table(MeetingSchedule)


def test_admin_change_start_time_rejects_invalid(admin_client):
    clear_table(MeetingSchedule)
    try:
        # Hour 25 should be rejected.
        r = admin_client.put(
            "/internal/schedule",
            json={"weekday": 0, "start_time": "25:00"},
        )
        assert r.status_code == 422

        # Non-numeric components should be rejected.
        r = admin_client.put(
            "/internal/schedule",
            json={"weekday": 0, "start_time": "abc:00"},
        )
        assert r.status_code == 422
    finally:
        clear_table(MeetingSchedule)
