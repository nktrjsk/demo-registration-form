"""AI-013: Non-admin users cannot change the schedule (API returns 403)."""
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import require_auth
from app.main import app
from app.models import MeetingSchedule
from tests.conftest import clear_table


async def _override_non_admin(request: Request):
    request.state.claims = {
        "preferred_username": "test-user",
        "email": "user@test.example",
        "group_membership": ["/NikitaPlay"],  # no /admin
    }


@pytest.fixture
def non_admin_client():
    app.dependency_overrides[require_auth] = _override_non_admin
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_non_admin_put_schedule_returns_403(non_admin_client):
    clear_table(MeetingSchedule)
    try:
        r = non_admin_client.put(
            "/internal/schedule",
            json={"weekday": 3, "start_time": "12:00"},
        )
        assert r.status_code == 403, r.text
        # GET is still allowed for non-admins.
        r = non_admin_client.get("/public/schedule")
        assert r.status_code == 200
        # Default still in effect since the PUT was rejected.
        assert r.json() == {"weekday": 0, "start_time": "15:00"}
    finally:
        clear_table(MeetingSchedule)
