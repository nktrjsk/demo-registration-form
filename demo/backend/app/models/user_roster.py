from datetime import datetime

from sqlalchemy import Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class UserRoster(Base):
    """Master roster of every email that has logged in via OIDC at least once.

    Used to derive each meeting's attendee list: a user appears on a
    meeting iff first_seen_at <= meeting_date (so they show up on the
    current and all future meetings, never retroactively on past ones).
    """

    __tablename__ = "user_roster"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
