from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bed import Bed
from app.models.owner import Owner
from app.models.property import Property
from app.models.room import Room
from app.schemas.room import RoomCreate, RoomUpdate, RoomWithBedsCreate
from app.services.access import (
    get_manageable_property,
    require_active_property,
    require_manageable_property,
)
from app.services.bed_service import build_beds
from app.services.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)

ROOM_CONFLICT_MESSAGE = (
    "A room with this number already exists in this property, or the "
    "room configuration is invalid."
)


@dataclass
class CreatedRoom:
    room: Room
    beds: list[Bed] = field(default_factory=list)


def validate_floor_number(
    property_obj: Property, floor_number: int | None
) -> None:
    """Floors run 0..total_floors-1 (0 = ground). None is always allowed."""
    if floor_number is None:
        return

    if property_obj.total_floors is None:
        raise DomainValidationError(
            "Set total_floors on the property before assigning room floors."
        )

    if floor_number >= property_obj.total_floors:
        raise DomainValidationError(
            f"floor_number {floor_number} is outside the property floors "
            f"(0 to {property_obj.total_floors - 1})."
        )


def add_rooms(
    db: Session,
    property_obj: Property,
    rooms_data: list[RoomCreate] | list[RoomWithBedsCreate],
) -> list[CreatedRoom]:
    """Validate, add and flush rooms (and any nested beds). Never commits.

    Shared by standalone bulk creation and nested property creation so the
    caller owns the transaction boundary. Raises ConflictError if the flush
    hits a unique/check constraint; the caller must roll back.
    """
    numbers = [room.room_number for room in rooms_data]
    if len(numbers) != len(set(numbers)):
        raise DomainValidationError(
            "Duplicate room numbers were provided. No rooms were created."
        )

    created: list[CreatedRoom] = []
    for room_data in rooms_data:
        validate_floor_number(property_obj, room_data.floor_number)

        room = Room(
            id=uuid4(),
            property_id=property_obj.id,
            **room_data.model_dump(exclude={"beds"}),
        )
        beds_data = getattr(room_data, "beds", [])
        created.append(CreatedRoom(room, build_beds(room.id, beds_data)))

    try:
        db.add_all([item.room for item in created])
        db.add_all([bed for item in created for bed in item.beds])
        db.flush()
    except IntegrityError as exc:
        raise ConflictError(ROOM_CONFLICT_MESSAGE) from exc

    return created


def create_rooms(
    db: Session,
    owner: Owner,
    property_id: UUID,
    data: RoomCreate | list[RoomCreate],
) -> list[Room]:
    property_obj = require_manageable_property(db, owner, property_id)
    require_active_property(property_obj, "add rooms")

    rooms_data = data if isinstance(data, list) else [data]

    try:
        created = add_rooms(db, property_obj, rooms_data)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return [item.room for item in created]


def list_property_rooms(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> list[Room]:
    property_obj = require_manageable_property(db, owner, property_id)

    statement = (
        select(Room)
        .where(
            Room.property_id == property_obj.id,
            Room.is_active.is_(True),
        )
        .order_by(Room.room_number.asc())
    )

    return list(db.scalars(statement).all())


def get_property_room(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
) -> Room | None:
    property_obj = get_manageable_property(db, owner, property_id)

    if property_obj is None:
        return None

    statement = select(Room).where(
        Room.id == room_id,
        Room.property_id == property_obj.id,
        Room.is_active.is_(True),
    )

    return db.scalar(statement)


def _get_room_for_update(
    db: Session, owner: Owner, property_id: UUID, room_id: UUID
) -> tuple[Property, Room]:
    property_obj = require_manageable_property(db, owner, property_id)
    room = db.scalar(
        select(Room).where(
            Room.id == room_id,
            Room.property_id == property_obj.id,
        )
    )
    if room is None:
        raise NotFoundError("Room not found.")
    return property_obj, room


def update_room(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
    data: RoomUpdate,
) -> Room:
    property_obj, room = _get_room_for_update(db, owner, property_id, room_id)

    update_data = data.model_dump(exclude_unset=True)

    if "floor_number" in update_data:
        validate_floor_number(property_obj, update_data["floor_number"])

    if update_data.get("is_active") is True and not room.is_active:
        # Reactivation: parent must be visible and the room must be priced.
        require_active_property(property_obj, "reactivate a room")
        price = update_data.get(
            "monthly_rent_per_bed", room.monthly_rent_per_bed
        )
        if price is None:
            raise DomainValidationError(
                "Set monthly_rent_per_bed before activating this room."
            )

    for field_name, value in update_data.items():
        setattr(room, field_name, value)

    try:
        db.commit()
        db.refresh(room)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(ROOM_CONFLICT_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise

    return room


def deactivate_room(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
) -> Room:
    _, room = _get_room_for_update(db, owner, property_id, room_id)
    room.is_active = False

    try:
        db.commit()
        db.refresh(room)
    except Exception:
        db.rollback()
        raise

    return room


def max_room_floor(db: Session, property_id: UUID) -> int | None:
    return db.scalar(
        select(func.max(Room.floor_number)).where(
            Room.property_id == property_id
        )
    )
