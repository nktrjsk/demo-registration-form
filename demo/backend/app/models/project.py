from datetime import datetime

from sqlalchemy import Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Project(Base):
    """Global catalog of projects.

    Persistent across Demo meetings; per-meeting notes live in
    ProjectEntry. Anyone signed in can create/rename/delete. The
    leader points to a Person row (which may be a placeholder until
    that person logs in via OIDC).
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    leader_person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
