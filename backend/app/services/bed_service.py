from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bed import Bed
from app.models.owner import Owner
from app.models.room import Room
from app.schemas.bed import BedCreate, BedUpdate
from app.services.access import (
    require_active_property,
    require_manageable_property,
)
from app.services.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)

BED_CONFLICT_MESSAGE = (
    "A bed with this label already exists in this room, or the bed "
    "configuration is invalid."
)


def build_beds(room_id: UUID, beds_data: list[BedCreate]) -> list[Bed]:
    """Validate and construct Bed objects for a room without touching the DB.

    Shared by standalone and nested creation so both enforce the same rules.
    """
    labels = [bed.bed_label for bed in beds_data]
    if len(labels) != len(set(labels)):
        raise DomainValidationError(
            "Duplicate bed labels were provided in the same room."
        )

    return [
        Bed(id=uuid4(), room_id=room_id, **bed.model_dump())
        for bed in beds_data
    ]


def _get_room(
    db: Session, owner: Owner, property_id: UUID, room_id: UUID
) -> Room:
    """Fetch a room through its owner-scoped property."""
    property_obj = require_manageable_property(db, owner, property_id)
    room = db.scalar(
        select(Room).where(
            Room.id == room_id,
            Room.property_id == property_obj.id,
        )
    )
    if room is None:
        raise NotFoundError("Room not found.")
    return room


def create_beds(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
    data: BedCreate | list[BedCreate],
) -> list[Bed]:
    property_obj = require_manageable_property(db, owner, property_id)
    require_active_property(property_obj, "add beds")

    room = _get_room(db, owner, property_id, room_id)
    if not room.is_active:
        raise ConflictError("Cannot add beds to an inactive room.")

    beds_data = data if isinstance(data, list) else [data]
    beds = build_beds(room.id, beds_data)

    try:
        db.add_all(beds)
        db.flush()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(BED_CONFLICT_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise

    return beds


def list_room_beds(
    db: Session, owner: Owner, property_id: UUID, room_id: UUID
) -> list[Bed]:
    room = _get_room(db, owner, property_id, room_id)
    return list(
        db.scalars(
            select(Bed).where(Bed.room_id == room.id).order_by(Bed.bed_label)
        ).all()
    )


def get_room_bed(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
    bed_id: UUID,
) -> Bed:
    room = _get_room(db, owner, property_id, room_id)
    bed = db.scalar(
        select(Bed).where(Bed.id == bed_id, Bed.room_id == room.id)
    )
    if bed is None:
        raise NotFoundError("Bed not found.")
    return bed


def update_bed(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
    bed_id: UUID,
    data: BedUpdate,
) -> Bed:
    bed = get_room_bed(db, owner, property_id, room_id, bed_id)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(bed, field, value)

    try:
        db.commit()
        db.refresh(bed)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(BED_CONFLICT_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise

    return bed


def deactivate_bed(
    db: Session,
    owner: Owner,
    property_id: UUID,
    room_id: UUID,
    bed_id: UUID,
) -> Bed:
    bed = get_room_bed(db, owner, property_id, room_id, bed_id)
    bed.status = "inactive"

    try:
        db.commit()
        db.refresh(bed)
    except Exception:
        db.rollback()
        raise

    return bed
