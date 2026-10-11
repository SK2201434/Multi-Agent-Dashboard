import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class BedChangeRequest(Base):
    """A tenant's ask to move a stay to another bed in the same property."""

    __tablename__ = "bed_change_requests"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_bed_change_requests_status",
        ),
        CheckConstraint(
            "from_bed_id <> to_bed_id",
            name="ck_bed_change_requests_distinct_beds",
        ),
        CheckConstraint(
            "(status = 'approved') = (effective_at IS NOT NULL)",
            name="ck_bed_change_requests_effective_at",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR "
            "(reviewed_at IS NOT NULL AND reviewed_by_owner_id IS NOT NULL)",
            name="ck_bed_change_requests_review_consistency",
        ),
        # One pending change per stay.
        Index(
            "uq_bed_change_requests_pending_stay",
            "stay_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_bed_change_requests_property_status",
            "property_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    stay_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stays.id"), nullable=False, index=True
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False
    )
    from_bed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("beds.id"), nullable=False
    )
    to_bed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("beds.id"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
