from datetime import time

from fastapi import Body, Depends, HTTPException
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import MeetingSchedule
from app.routers.internal import router


def _parse_start_time(value: str) -> time:
    parts = value.split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise HTTPException(status_code=422, detail="start_time must be HH:MM or HH:MM:SS")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        raise HTTPException(status_code=422, detail="start_time has non-integer components")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise HTTPException(status_code=422, detail="start_time out of range")
    return time(hh, mm)


@router.put("/schedule", dependencies=[Depends(require_admin)])
async def put_schedule(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    if "weekday" not in payload or "start_time" not in payload:
        raise HTTPException(status_code=422, detail="weekday and start_time required")
    try:
        weekday = int(payload["weekday"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="weekday must be an integer 0..6")
    if not 0 <= weekday <= 6:
        raise HTTPException(status_code=422, detail="weekday must be 0..6 (Mon=0..Sun=6)")
    start_time = _parse_start_time(str(payload["start_time"]))

    stmt = (
        insert(MeetingSchedule)
        .values(id=1, weekday=weekday, start_time=start_time)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"weekday": weekday, "start_time": start_time},
        )
    )
    await db.execute(stmt)
    await db.commit()
    return {"weekday": weekday, "start_time": start_time.strftime("%H:%M")}
