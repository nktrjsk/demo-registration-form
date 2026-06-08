"""AI-010: Fresh deployment defaults to Monday 15:00 schedule."""
import httpx

from app.models import MeetingSchedule
from tests.conftest import clear_table


BACKEND_URL = "http://localhost:8080"


def test_default_schedule_is_monday_15():
    clear_table(MeetingSchedule)
    r = httpx.get(f"{BACKEND_URL}/public/schedule")
    assert r.status_code == 200
    assert r.json() == {"weekday": 0, "start_time": "15:00"}
