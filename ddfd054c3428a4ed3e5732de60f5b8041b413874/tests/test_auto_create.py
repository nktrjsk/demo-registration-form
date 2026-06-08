"""Tests for the midnight auto-create job.

Covers:
  AI-014: At 00:00 on the configured demo weekday, a row is created.
  AI-015: Idempotent — re-running the same day does not duplicate.
  AI-016: Changing the weekday makes the next auto-create occur on that day.
"""
from datetime import datetime, date, time

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.auto_create import LOCAL_TZ, create_if_demo_day
from app.main import app
from app.models import MeetingInstance, MeetingSchedule
from tests.conftest import clear_table, db_run


# Calendar anchors used in tests
MONDAY = datetime(2026, 6, 1, 0, 0)      # 1 June 2026 — a Monday
TUESDAY = datetime(2026, 6, 2, 0, 0)     # 2 June 2026
WEDNESDAY = datetime(2026, 6, 3, 0, 0)   # 3 June 2026


def _localize(dt: datetime) -> datetime:
    return LOCAL_TZ.localize(dt)


def _list_instances():
    async def _do(session):
        result = await session.execute(
            select(MeetingInstance.meeting_date).order_by(MeetingInstance.meeting_date)
        )
        return [row[0] for row in result.all()]

    return db_run(_do)


def _set_schedule(weekday: int) -> None:
    async def _do(session):
        stmt = (
            insert(MeetingSchedule)
            .values(id=1, weekday=weekday, start_time=time(15, 0))
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"weekday": weekday, "start_time": time(15, 0)},
            )
        )
        await session.execute(stmt)
        await session.commit()

    db_run(_do)


def _run_tick(now: datetime):
    async def _do(session):
        return await create_if_demo_day(now, session)

    return db_run(_do)


def test_creates_instance_on_demo_day():
    """AI-014: Tick on Monday (default weekday) creates a meeting row dated today."""
    clear_table(MeetingInstance)
    clear_table(MeetingSchedule)  # defaults => Monday

    result = _run_tick(_localize(MONDAY))

    assert result == date(2026, 6, 1)
    assert _list_instances() == [date(2026, 6, 1)]


def test_no_instance_on_non_demo_day():
    """Non-demo day: tick is a no-op."""
    clear_table(MeetingInstance)
    clear_table(MeetingSchedule)  # defaults => Monday

    result = _run_tick(_localize(TUESDAY))

    assert result is None
    assert _list_instances() == []


def test_tick_is_idempotent_on_same_day():
    """AI-015: Re-running on the same demo day does not duplicate."""
    clear_table(MeetingInstance)
    clear_table(MeetingSchedule)

    _run_tick(_localize(MONDAY))
    _run_tick(_localize(MONDAY))
    _run_tick(_localize(MONDAY))

    assert _list_instances() == [date(2026, 6, 1)]


def test_schedule_change_redirects_auto_create():
    """AI-016: After admin moves the demo weekday to Wednesday, the next
    tick on a Wednesday creates that day's instance, and a tick on the old
    weekday (Monday) does not.
    """
    clear_table(MeetingInstance)
    _set_schedule(2)  # Wednesday

    # Old weekday no longer triggers creation.
    assert _run_tick(_localize(MONDAY)) is None
    assert _list_instances() == []

    # New weekday does.
    assert _run_tick(_localize(WEDNESDAY)) == date(2026, 6, 3)
    assert _list_instances() == [date(2026, 6, 3)]

    clear_table(MeetingSchedule)
    clear_table(MeetingInstance)


def test_startup_backfills_when_today_is_demo_day(monkeypatch):
    """Regression: APScheduler's cron does not backfill a missed firing.
    If the backend restarts past 00:00 on a demo day, the startup hook
    must still create today's instance."""
    monkeypatch.delenv("BITSWAN_DISABLE_SCHEDULER", raising=False)
    today = date.today()
    _set_schedule(today.weekday())
    clear_table(MeetingInstance)
    try:
        with TestClient(app):
            pass  # __enter__ runs FastAPI startup → catch_up_today
        assert today in _list_instances()
    finally:
        clear_table(MeetingInstance)
        clear_table(MeetingSchedule)


def test_startup_noop_when_today_is_not_demo_day(monkeypatch):
    monkeypatch.delenv("BITSWAN_DISABLE_SCHEDULER", raising=False)
    today = date.today()
    _set_schedule((today.weekday() + 1) % 7)  # schedule = tomorrow's weekday
    clear_table(MeetingInstance)
    try:
        with TestClient(app):
            pass
        assert _list_instances() == []
    finally:
        clear_table(MeetingInstance)
        clear_table(MeetingSchedule)
