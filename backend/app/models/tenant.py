import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Tenant(Base):
    """Global tenant identity, independent of any property or owner.

    Property membership lives in PropertyTenant. Email (normalized to
    lower case) is the unique login identity; phone is contact-only and
    intentionally not unique.
    """

    __tablename__ = "tenants"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_activation', 'active')",
            name="ck_tenants_status",
        ),
        # Pending accounts have no credentials; active accounts must.
        CheckConstraint(
            "(status = 'pending_activation' AND password_hash IS NULL "
            "AND activated_at IS NULL) OR "
            "(status = 'active' AND password_hash IS NOT NULL "
            "AND activated_at IS NOT NULL)",
            name="ck_tenants_activation_consistency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    full_name: Mapped[str] = mapped_column(String(150), nullable=False)

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )

    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending_activation",
        nullable=False,
    )

    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Embedded in tenant JWTs; incremented on password change so every
    # earlier token stops validating. Never client- or owner-writable.
    token_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
