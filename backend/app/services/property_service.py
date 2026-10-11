
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.owner import Owner
from app.models.property import Property
from app.schemas.property import PropertyCreate, PropertyUpdate



def create_property(
    db: Session,
    owner: Owner,
    data: PropertyCreate,
) -> Property:
    property_obj = Property(
        **data.model_dump(),
        owner_id=owner.id,
    )

    try:
        db.add(property_obj)
        db.commit()
        db.refresh(property_obj)
    except Exception:
        db.rollback()
        raise

    return property_obj



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

    non_nullable_fields = {
        "name",
        "property_type",
        "address",
        "city",
        "state",
        "postal_code",
        "is_active",
    }

    for field in non_nullable_fields:
        if field in update_data and update_data[field] is None:
            raise ValueError(f"{field} cannot be null.")

    for field, value in update_data.items():
        setattr(property_obj, field, value)

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

    property_obj.deleted_at = datetime.now(timezone.utc)
    property_obj.is_active = False

    try:
        db.commit()
        db.refresh(property_obj)
    except Exception:
        db.rollback()
        raise

    return property_obj
