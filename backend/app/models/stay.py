
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
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Stay(Base):
    __tablename__ = "stays"

    __table_args__ = (
        CheckConstraint(
            "move_out_date IS NULL OR move_out_date >= move_in_date",
            name="ck_stays_valid_dates",
        ),
        CheckConstraint(
            "monthly_rent >= 0",
            name="ck_stays_monthly_rent_non_negative",
        ),
        # A bed can have only one ongoing stay.
        Index(
            "uq_stays_bed_active",
            "bed_id",
            unique=True,
            postgresql_where=text("move_out_date IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )

    bed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("beds.id"),
        nullable=False,
        index=True,
    )

    move_in_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    move_out_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    monthly_rent: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
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