import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Notification(Base):
    """In-app notification for exactly one owner or one tenant.

    dedupe_key is unique per recipient, so a retried or repeated trigger
    can never produce a second copy.
    """

    __tablename__ = "notifications"

    __table_args__ = (
        CheckConstraint(
            "(owner_id IS NOT NULL AND tenant_id IS NULL) OR "
            "(owner_id IS NULL AND tenant_id IS NOT NULL)",
            name="ck_notifications_single_recipient",
        ),
        Index(
            "uq_notifications_owner_dedupe", "owner_id", "dedupe_key",
            unique=True, postgresql_where=text("owner_id IS NOT NULL"),
        ),
        Index(
            "uq_notifications_tenant_dedupe", "tenant_id", "dedupe_key",
            unique=True, postgresql_where=text("tenant_id IS NOT NULL"),
        ),
        Index("ix_notifications_owner_created", "owner_id", "created_at"),
        Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rent_obligations.id"), nullable=True
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_claims.id"), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
