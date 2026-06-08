from datetime import datetime

from sqlalchemy import Integer, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Person(Base):
    """A person known to the automation, resolved or not.

    - `email IS NULL`  ⇒ placeholder typed in by someone (e.g. an admin set
      it as a project leader before that user ever logged in).
    - `email IS NOT NULL` ⇒ resolved person; either created at OIDC login or
      promoted from a placeholder when the display_name matched a login
      claim (see app.auth.record_login).

    `email` is unique when set; `display_name` is intentionally non-unique
    (two real Johns can coexist; ambiguity is resolved best-effort by
    pairing the oldest unpaired placeholder).
    """

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
