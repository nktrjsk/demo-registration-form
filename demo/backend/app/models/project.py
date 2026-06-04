from datetime import datetime

from sqlalchemy import Integer, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Project(Base):
    """Global catalog of projects.

    Projects persist across Demo meetings — the leader and identity stay
    stable while per-meeting notes live in ProjectEntry. Anyone signed in
    can create/rename/delete projects.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    leader: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
