import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class StayBedAssignment(Base):
    """One bed held by a stay for a span of time. History is append-only.

    * Current assignment: released_at IS NULL (exactly one per live stay).
    * Planned allocation: occupied_from IS NULL. The bed is held for
      [period_start, period_end) but nobody is physically there yet.
    * Physical occupancy: occupied_from set (check-in or an applied move)
      until released_at.
    Moves never edit a row's bed: the old row is released and a new row is
    inserted, so [assigned_at, released_at) intervals of a stay tile
    without overlap.
    """

    __tablename__ = "stay_bed_assignments"

    __table_args__ = (
        CheckConstraint(
            "period_end IS NULL OR period_end > period_start",
            name="ck_stay_bed_assignments_period",
        ),
        CheckConstraint(
            "released_at IS NULL OR released_at >= assigned_at",
            name="ck_stay_bed_assignments_released_after_assigned",
        ),
        CheckConstraint(
            "occupied_from IS NULL OR occupied_from >= assigned_at",
            name="ck_stay_bed_assignments_occupied_after_assigned",
        ),
        CheckConstraint(
            "released_at IS NULL OR occupied_from IS NULL "
            "OR released_at >= occupied_from",
            name="ck_stay_bed_assignments_released_after_occupied",
        ),
        # One current assignment per stay.
        Index(
            "uq_stay_bed_assignments_current_stay",
            "stay_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        # One physical occupant per bed, whatever the planned dates say.
        Index(
            "uq_stay_bed_assignments_bed_occupied",
            "bed_id",
            unique=True,
            postgresql_where=text(
                "released_at IS NULL AND occupied_from IS NOT NULL"
            ),
        ),
        # No two current allocations of a bed may overlap in date.
        ExcludeConstraint(
            ("bed_id", "="),
            (text("daterange(period_start, period_end, '[)')"), "&&"),
            name="ex_stay_bed_assignments_bed_no_overlap",
            using="gist",
            where=text("released_at IS NULL"),
        ),
        # A stay's own assignment intervals never overlap in time.
        ExcludeConstraint(
            ("stay_id", "="),
            (text("tstzrange(assigned_at, released_at, '[)')"), "&&"),
            name="ex_stay_bed_assignments_stay_timeline",
            using="gist",
        ),
        Index("ix_stay_bed_assignments_bed_current", "bed_id", "released_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    stay_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stays.id"), nullable=False, index=True
    )
    bed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("beds.id"), nullable=False
    )

    # Date interval the bed is held for (copied from the stay when the row
    # is created). NULL period_end = open-ended.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    occupied_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The approved bed change that created this assignment, if any.
    bed_change_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bed_change_requests.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
