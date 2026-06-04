"""Auto-creates the Demo meeting instance at midnight on each demo weekday.

Schedule is read from the `meeting_schedule` table on each tick, so admin
changes take effect on the very next midnight.

`create_if_demo_day` is parameterised on session + now to keep it
testable: the scheduler passes the app's session and current time, tests
pass a fresh-engine session and a controlled `now`.
"""
import logging
import os
from datetime import date, datetime

import pytz
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MeetingInstance, MeetingSchedule
from app.models.meeting_schedule import DEFAULT_WEEKDAY


logger = logging.getLogger(__name__)

LOCAL_TZ_NAME = os.environ.get("DEMO_LOCAL_TZ", "Europe/Prague")
LOCAL_TZ = pytz.timezone(LOCAL_TZ_NAME)


async def _current_weekday(session: AsyncSession) -> int:
    result = await session.execute(
        select(MeetingSchedule.weekday).where(MeetingSchedule.id == 1)
    )
    row = result.scalar_one_or_none()
    return DEFAULT_WEEKDAY if row is None else row


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


async def create_if_demo_day(now: datetime, session: AsyncSession) -> date | None:
    """If `now`'s date falls on the configured demo weekday, ensure a
    meeting instance exists for that date. Returns the date if today is a
    demo day (whether the row was just created or already existed), or
    None otherwise.

    Idempotent — relies on the meeting_instances unique constraint on
    meeting_date plus ON CONFLICT DO NOTHING.
    """
    today = now.date()
    weekday = await _current_weekday(session)
    if today.weekday() != weekday:
        return None

    stmt = (
        insert(MeetingInstance)
        .values(meeting_date=today)
        .on_conflict_do_nothing(index_elements=["meeting_date"])
    )
    await session.execute(stmt)
    await session.commit()
    return today
