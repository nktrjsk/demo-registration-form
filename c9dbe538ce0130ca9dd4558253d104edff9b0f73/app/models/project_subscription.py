from datetime import datetime

from sqlalchemy import Integer, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class ProjectSubscription(Base):
    """A user's subscription to a project (their 'my projects' list).

    Auto-created when a user first writes a note for a project they
    haven't yet subscribed to. Subscriptions don't gate access — anyone
    can write notes on any project. They're a UX shortcut for sorting
    the form.
    """

    __tablename__ = "project_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_email", "project_id", name="uq_project_subscriptions_user_project"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
