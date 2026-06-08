from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import MeetingSchedule
from app.models.meeting_schedule import DEFAULT_WEEKDAY, DEFAULT_START_TIME
from app.routers.public import router


def _format(weekday: int, start_time) -> dict:
    return {
        "weekday": weekday,
        "start_time": start_time.strftime("%H:%M"),
    }


@router.get("/schedule")
async def get_schedule(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MeetingSchedule).where(MeetingSchedule.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        return _format(DEFAULT_WEEKDAY, DEFAULT_START_TIME)
    return _format(row.weekday, row.start_time)
