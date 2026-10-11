
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.booking_request import BookingRequest
from app.models.owner import Owner
from app.models.property import Property
from app.models.stay import LIVE_STAY_STATUSES, Stay
from app.schemas.property import PropertyCreate, PropertyUpdate
from app.services import room_service
from app.services.errors import ConflictError, DomainValidationError


@dataclass
class CreatedProperty:
    property: Property
    rooms: list[room_service.CreatedRoom] = field(default_factory=list)


def create_property(
    db: Session,
    owner: Owner,
    data: PropertyCreate,
) -> CreatedProperty:
    """Create a property and any nested rooms/beds in one transaction."""
    property_obj = Property(
        **data.model_dump(exclude={"rooms"}),
        owner_id=owner.id,
    )

    try:
        db.add(property_obj)

        created_rooms: list[room_service.CreatedRoom] = []
        if data.rooms:
            # Flush so the property row exists, then add rooms and beds
            # in the same transaction. Nothing is committed until the end.
            db.flush()
            created_rooms = room_service.add_rooms(
                db, property_obj, data.rooms
            )

        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "The property configuration conflicts with existing data "
            "or is invalid. Nothing was created."
        ) from exc
    except Exception:
        db.rollback()
        raise

    return CreatedProperty(property_obj, created_rooms)


def list_owner_properties(
    db: Session,
    owner: Owner,
) -> list[Property]:
    statement = (
        select(Property)
        .where(
            Property.owner_id == owner.id,
            Property.deleted_at.is_(None),
        )
        .order_by(Property.created_at.desc())
    )

    return list(db.scalars(statement).all())


def get_owner_property(
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



def update_property(
    db: Session,
    owner: Owner,
    property_id: UUID,
    data: PropertyUpdate,
) -> Property | None:
    property_obj = get_owner_property(db, owner, property_id)

    if property_obj is None:
        return None

    update_data = data.model_dump(exclude_unset=True)

    if "total_floors" in update_data:
        new_total = update_data["total_floors"]
        highest_floor = room_service.max_room_floor(db, property_obj.id)
        if highest_floor is not None and (
            new_total is None or new_total <= highest_floor
        ):
            raise DomainValidationError(
                f"total_floors must cover existing room floors "
                f"(highest used floor is {highest_floor})."
            )

    non_nullable_fields = {
        "name",
        "property_type",
        "address",
        "city",
        "state",
        "postal_code",
        "is_active",
    }

    for name in non_nullable_fields:
        if name in update_data and update_data[name] is None:
            raise DomainValidationError(f"{name} cannot be null.")

    for name, value in update_data.items():
        setattr(property_obj, name, value)

    try:
        db.commit()
        db.refresh(property_obj)
    except Exception:
        db.rollback()
        raise

    return property_obj



def hide_property(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> Property | None:
    property_obj = get_owner_property(db, owner, property_id)

    if property_obj is None:
        return None

    property_obj.is_active = False
    db.commit()
    db.refresh(property_obj)

    return property_obj


def soft_delete_property(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> Property | None:
    property_obj = get_owner_property(db, owner, property_id)

    if property_obj is None:
        return None

    live_stay = db.scalar(
        select(Stay.id)
        .where(
            Stay.property_id == property_obj.id,
            Stay.status.in_(LIVE_STAY_STATUSES),
        )
        .limit(1)
    )
    if live_stay is not None:
        raise ConflictError(
            "Cannot delete a property with reserved or checked-in stays. "
            "Cancel or check them out first."
        )

    # Nothing can be approved for a deleted property; do not leave tenants'
    # requests pending forever. Historical stays/requests stay in place.
    db.execute(
        update(BookingRequest)
        .where(
            BookingRequest.property_id == property_obj.id,
            BookingRequest.status == "pending",
        )
        .values(
            status="expired",
            rejection_reason="The property is no longer available.",
            updated_at=datetime.now(timezone.utc),
        )
    )

    property_obj.deleted_at = datetime.now(timezone.utc)
    property_obj.is_active = False

    try:
        db.commit()
        db.refresh(property_obj)
    except Exception:
        db.rollback()
        raise

    return property_obj
