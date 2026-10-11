"""Public room and bed availability, computed from authoritative rows.

Pending booking and bed-change requests are never consulted: they hold
nothing. Only current bed assignments of live stays (and the operational
status of the bed, room and property) decide availability.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bed import Bed
from app.models.property import Property
from app.models.room import Room
from app.schemas.stay import (
    PublicAvailabilityResponse,
    PublicBedAvailability,
    PublicRoomAvailability,
    PublicRoomResponse,
)
from app.services import stay_rules as rules
from app.services.errors import NotFoundError


def _bookable_rows(db: Session, property_id: UUID) -> list[tuple[Room, Bed]]:
    property_obj = db.scalar(
        select(Property).where(
            Property.id == property_id,
            Property.is_active.is_(True),
            Property.deleted_at.is_(None),
        )
    )
    if property_obj is None:
        raise NotFoundError("Property not found.")

    # Rooms without a configured rent cannot be booked, so are not listed.
    rows = db.execute(
        select(Room, Bed)
        .join(Bed, Bed.room_id == Room.id)
        .where(
            Room.property_id == property_id,
            Room.is_active.is_(True),
            Room.monthly_rent_per_bed.is_not(None),
            Bed.status == "available",
        )
        .order_by(Room.floor_number, Room.room_number, Bed.bed_label)
    ).all()
    return [tuple(row) for row in rows]


def _group(rows: list[tuple[Room, Bed]]) -> dict[UUID, tuple[Room, list[Bed]]]:
    grouped: dict[UUID, tuple[Room, list[Bed]]] = {}
    for room, bed in rows:
        grouped.setdefault(room.id, (room, []))[1].append(bed)
    return grouped


def list_rooms(db: Session, property_id: UUID) -> list[PublicRoomResponse]:
    return [
        PublicRoomResponse(
            room_id=room.id,
            floor_number=room.floor_number,
            room_number=room.room_number,
            room_type=room.room_type,
            ac_type=room.ac_type,
            sharing_type=room.sharing_type,
            monthly_rent_per_bed=room.monthly_rent_per_bed,
            total_bookable_beds=len(beds),
        )
        for room, beds in _group(_bookable_rows(db, property_id)).values()
    ]


def availability(
    db: Session,
    property_id: UUID,
    check_in: date,
    check_out: date | None,
) -> PublicAvailabilityResponse:
    rules.validate_dates(check_in, check_out)
    rows = _bookable_rows(db, property_id)
    blocked = rules.blocked_bed_ids(
        db, [bed.id for _, bed in rows], check_in, check_out
    )

    rooms: list[PublicRoomAvailability] = []
    for room, beds in _group(rows).values():
        bed_items = [
            PublicBedAvailability(
                bed_id=bed.id,
                bed_label=bed.bed_label,
                available=bed.id not in blocked,
            )
            for bed in beds
        ]
        rooms.append(
            PublicRoomAvailability(
                room_id=room.id,
                floor_number=room.floor_number,
                room_number=room.room_number,
                room_type=room.room_type,
                ac_type=room.ac_type,
                sharing_type=room.sharing_type,
                monthly_rent_per_bed=room.monthly_rent_per_bed,
                total_bookable_beds=len(beds),
                available_beds=sum(item.available for item in bed_items),
                beds=bed_items,
            )
        )

    return PublicAvailabilityResponse(
        property_id=property_id,
        check_in=check_in,
        check_out=check_out,
        total_bookable_beds=sum(r.total_bookable_beds for r in rooms),
        total_available_beds=sum(r.available_beds for r in rooms),
        rooms=rooms,
    )
