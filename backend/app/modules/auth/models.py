"""
User model and role definitions.
"""

import uuid
import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import String, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.modules.exchange.models import ExchangeConnection  # noqa
else:
    # Ensure ExchangeConnection is imported at runtime for mapper initialization
    try:
        from app.modules.exchange.models import ExchangeConnection  # noqa
    except ImportError:
        pass


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    PREMIUM = "premium"
    BASIC = "basic"
    GUEST = "guest"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))

    # Profile
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    country: Mapped[str] = mapped_column(String(100), default="")
    language: Mapped[str] = mapped_column(String(10), default="en")
    currency: Mapped[str] = mapped_column(String(10), default="USD")

    # Role & Status — store the .value (lowercase) strings in DB
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, values_callable=lambda e: [m.value for m in e], native_enum=False),
        default=UserRole.BASIC,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    exchange_connections = relationship("ExchangeConnection", back_populates="user", lazy="selectin")
    watchlists = relationship("Watchlist", back_populates="user", lazy="selectin")

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role.value})>"
