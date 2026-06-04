from datetime import datetime

from sqlalchemy import Integer, Text, DateTime, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class MeetingEntry(Base):
    """One user's record for one meeting: did they attend?"""

    __tablename__ = "meeting_entries"
    __table_args__ = (
        UniqueConstraint(
            "meeting_instance_id", "user_email", name="uq_meeting_entries_meeting_user"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_instance_id: Mapped[int] = mapped_column(
        ForeignKey("meeting_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    attended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectEntry(Base):
    """One user's free-text note on one project at one meeting."""

    __tablename__ = "project_entries"
    __table_args__ = (
        UniqueConstraint(
            "meeting_entry_id", "project_id", name="uq_project_entries_entry_project"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_entry_id: Mapped[int] = mapped_column(
        ForeignKey("meeting_entries.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
