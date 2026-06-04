from datetime import datetime

from sqlalchemy import Integer, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Project(Base):
    """A project listed under a specific Demo meeting. Scoped per meeting."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("meeting_instance_id", "name", name="uq_projects_meeting_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_instance_id: Mapped[int] = mapped_column(
        ForeignKey("meeting_instances.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    leader: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
