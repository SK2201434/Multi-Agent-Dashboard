
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Room(Base):
    __tablename__ = "rooms"

    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "room_number",
            name="uq_rooms_property_room_number",
        ),
        CheckConstraint(
            "ac_type IN ('ac', 'non_ac')",
            name="ck_rooms_ac_type",
        ),
        CheckConstraint(
            "sharing_type IN ('single', 'double', 'triple', 'quad', 'other')",
            name="ck_rooms_sharing_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id"),
        nullable=False,
        index=True,
    )
    floor_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    monthly_rent_per_bed: Mapped[float | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    room_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    room_type: Mapped[str] = mapped_column(
        String(50),
        default="standard",
        nullable=False,
    )

    ac_type: Mapped[str] = mapped_column(
        String(10),
        default="non_ac",
        nullable=False,
    )

    sharing_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
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