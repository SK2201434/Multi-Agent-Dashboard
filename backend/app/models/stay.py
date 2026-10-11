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
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

STAY_STATUSES = ("reserved", "checked_in", "checked_out", "cancelled", "no_show")
# Statuses that hold a bed allocation (a current StayBedAssignment exists).
LIVE_STAY_STATUSES = ("reserved", "checked_in")


class Stay(Base):
    """A confirmed reservation and, after check-in, the physical occupancy.

    The bed lives in StayBedAssignment so moves keep history. Planned dates
    are calendar dates forming the half-open interval
    [planned_check_in, planned_check_out); a NULL check-out is open-ended.
    """

    __tablename__ = "stays"

    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'checked_in', 'checked_out', "
            "'cancelled', 'no_show')",
            name="ck_stays_status",
        ),
        CheckConstraint(
            "planned_check_out IS NULL OR planned_check_out > planned_check_in",
            name="ck_stays_planned_dates",
        ),
        CheckConstraint(
            "monthly_rent >= 0",
            name="ck_stays_monthly_rent_non_negative",
        ),
        CheckConstraint(
            "(status = 'reserved' AND actual_check_in_at IS NULL "
            "AND actual_check_out_at IS NULL AND closed_at IS NULL) OR "
            "(status = 'checked_in' AND actual_check_in_at IS NOT NULL "
            "AND actual_check_out_at IS NULL AND closed_at IS NULL) OR "
            "(status = 'checked_out' AND actual_check_in_at IS NOT NULL "
            "AND actual_check_out_at IS NOT NULL "
            "AND actual_check_out_at >= actual_check_in_at "
            "AND closed_at IS NULL) OR "
            "(status IN ('cancelled', 'no_show') "
            "AND actual_check_in_at IS NULL "
            "AND actual_check_out_at IS NULL AND closed_at IS NOT NULL)",
            name="ck_stays_lifecycle_consistency",
        ),
        # A tenant is physically in one bed at a time. Planned dates cannot
        # express this (a stay past its planned check-out is still here), so
        # this index, not the exclusion constraint below, covers that case.
        Index(
            "uq_stays_tenant_checked_in",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'checked_in'"),
        ),
        # A tenant cannot hold two live stays whose planned intervals
        # overlap, even on different beds. Final line of defence behind the
        # tenant row lock taken by the services.
        ExcludeConstraint(
            ("tenant_id", "="),
            (text("daterange(planned_check_in, planned_check_out, '[)')"), "&&"),
            name="ex_stays_tenant_no_overlap",
            using="gist",
            where=text("status IN ('reserved', 'checked_in')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False, index=True
    )

    # The approved request this stay came from; NULL for legacy rows.
    booking_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("booking_requests.id"),
        nullable=True,
        unique=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), default="reserved", nullable=False, index=True
    )

    planned_check_in: Mapped[date] = mapped_column(Date, nullable=False)
    planned_check_out: Mapped[date | None] = mapped_column(Date, nullable=True)

    actual_check_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_check_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Agreed monthly rent, snapshotted when the reservation was confirmed.
    monthly_rent: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Set when a reserved stay is cancelled or marked no-show.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    checked_in_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    checked_out_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    closed_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
