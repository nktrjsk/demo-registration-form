from datetime import time

from sqlalchemy import Integer, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


DEFAULT_WEEKDAY = 0  # Monday (Python ISO weekday convention: Monday=0..Sunday=6)
DEFAULT_START_TIME = time(15, 0)


class MeetingSchedule(Base):
    """Singleton row (id=1) holding the current Demo meeting schedule."""

    __tablename__ = "meeting_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
