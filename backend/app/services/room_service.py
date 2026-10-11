
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.owner import Owner
from app.models.property import Property
from app.models.room import Room
from app.schemas.room import RoomCreate, RoomUpdate


def get_manageable_property(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> Property | None:
    statement = select(Property).where(
        Property.id == property_id,
        Property.owner_id == owner.id,
        Property.deleted_at.is_(None),
    )

    return db.scalar(statement)


def create_room(
    db: Session,
    owner: Owner,
    property_id: UUID,
    data: RoomCreate,
) -> Room:
    property_obj = get_manageable_property(db, owner, property_id)

    if property_obj is None:
        raise ValueError("Property not found.")

    if not property_obj.is_active:
        raise ValueError("Cannot add rooms to a hidden property.")

    room = Room(
        **data.model_dump(),
        property_id=property_obj.id,
    )

    try:
        db.add(room)
        db.commit()
        db.refresh(room)
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "A room with this number already exists in this property, "
            "or the room configuration is invalid."
        ) from exc
    except Exception:
        db.rollback()
        raise

    return room


def list_property_rooms(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> list[Room]:
    property_obj = get_manageable_property(db, owner, property_id)

    if property_obj is None:
        raise ValueError("Property not found.")

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


def update_room(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
    data: RoomUpdate,
) -> Room | None:
    property_obj = get_manageable_property(db, owner, property_id)

    if property_obj is None:
        return None

    room = db.scalar(
        select(Room).where(
            Room.id == room_id,
            Room.property_id == property_obj.id,
        )
    )

    if room is None:
        return None

    update_data = data.model_dump(exclude_unset=True)

    non_nullable_fields = {
        "room_number",
        "room_type",
        "ac_type",
        "sharing_type",
        "is_active",
    }

    for field in non_nullable_fields:
        if field in update_data and update_data[field] is None:
            raise ValueError(f"{field} cannot be null.")

    if not room.is_active and update_data.get("is_active") is True:
        # Allow reactivation only if the parent property is active.
        if not property_obj.is_active:
            raise ValueError("Cannot reactivate a room in a hidden property.")

    for field, value in update_data.items():
        setattr(room, field, value)

    try:
        db.commit()
        db.refresh(room)
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "Room update violates a constraint. Check the room number "
            "and configuration."
        ) from exc
    except Exception:
        db.rollback()
        raise

    return room


def deactivate_room(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
) -> Room | None:
    property_obj = get_manageable_property(db, owner, property_id)

    if property_obj is None:
        return None

    room = db.scalar(
        select(Room).where(
            Room.id == room_id,
            Room.property_id == property_obj.id,
        )
    )

    if room is None:
        return None

    room.is_active = False

    try:
        db.commit()
        db.refresh(room)
    except Exception:
        db.rollback()
        raise

    return room

def create_rooms(db, owner, property_id, data: RoomCreate | list[RoomCreate]):
    property_obj = get_manageable_property(db, owner, property_id)

    if property_obj is None:
        raise ValueError("Property not found.")

    if not property_obj.is_active:
        raise ValueError("Cannot add rooms to a hidden property.")

    # Convert a single room into a one-item list for shared processing.
    rooms_data = data if isinstance(data, list) else [data]

    # Protect against duplicate room numbers inside the same request.
    room_numbers = [room.room_number for room in rooms_data]

    if len(room_numbers) != len(set(room_numbers)):
        raise ValueError(
            "Duplicate room numbers were provided. No rooms were created."
        )

    rooms = [
        Room(**room_data.model_dump(), property_id=property_obj.id)
        for room_data in rooms_data
    ]

    try:
        db.add_all(rooms)

        # Check database constraints before committing.
        db.flush()
        db.commit()

        for room in rooms:
            db.refresh(room)

        return rooms

    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "One or more room numbers already exist in this property, "
            "or a room configuration is invalid. No rooms were created."
        ) from exc

    except Exception:
        db.rollback()
        raise
