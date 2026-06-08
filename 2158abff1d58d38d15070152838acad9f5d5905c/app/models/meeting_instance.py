from datetime import datetime, date

from sqlalchemy import Integer, Date, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class MeetingInstance(Base):
    """One row per concrete demo meeting (past, present, or future)."""

    __tablename__ = "meeting_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
