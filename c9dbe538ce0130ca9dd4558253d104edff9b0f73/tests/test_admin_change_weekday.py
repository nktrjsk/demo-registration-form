"""AI-011: Admin can change the demo weekday; change persists in the DB."""
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


def test_admin_can_change_weekday(admin_client):
    clear_table(MeetingSchedule)
    try:
        # Default state: GET returns Monday/15:00
        r = admin_client.get("/public/schedule")
        assert r.status_code == 200
        assert r.json() == {"weekday": 0, "start_time": "15:00"}

        # Admin changes weekday to Wednesday (2), keeping start_time.
        r = admin_client.put(
            "/internal/schedule",
            json={"weekday": 2, "start_time": "15:00"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"weekday": 2, "start_time": "15:00"}

        # GET reflects the change.
        r = admin_client.get("/public/schedule")
        assert r.status_code == 200
        assert r.json() == {"weekday": 2, "start_time": "15:00"}

        # The DB row exists with the new weekday — proving persistence.
        row = _fetch_row()
        assert row is not None
        assert row.weekday == 2
        assert row.start_time.strftime("%H:%M") == "15:00"
    finally:
        clear_table(MeetingSchedule)
