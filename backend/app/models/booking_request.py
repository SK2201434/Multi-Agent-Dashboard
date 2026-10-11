import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class BookingRequest(Base):
    """A tenant's ask for a bed. It never reserves anything by itself."""

    __tablename__ = "booking_requests"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', "
            "'expired')",
            name="ck_booking_requests_status",
        ),
        CheckConstraint(
            "planned_check_out IS NULL OR planned_check_out > planned_check_in",
            name="ck_booking_requests_planned_dates",
        ),
        CheckConstraint(
            "quoted_monthly_rent >= 0",
            name="ck_booking_requests_quoted_rent_non_negative",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR "
            "(reviewed_at IS NOT NULL AND reviewed_by_owner_id IS NOT NULL)",
            name="ck_booking_requests_review_consistency",
        ),
        # The same tenant cannot stack identical pending requests.
        Index(
            "uq_booking_requests_pending_tenant_bed_start",
            "tenant_id",
            "bed_id",
            "planned_check_in",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_booking_requests_property_status",
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
    bed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("beds.id"), nullable=False, index=True
    )
    # Derived from the bed's room when the request is created.
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False
    )

    planned_check_in: Mapped[date] = mapped_column(Date, nullable=False)
    planned_check_out: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Server-computed from the room's rent at request time; informational.
    # The agreed rent is read from the room again at approval.
    quoted_monthly_rent: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )
    tenant_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
