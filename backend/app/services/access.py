from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.owner import Owner
from app.models.property import Property
from app.services.errors import ConflictError, NotFoundError


def get_manageable_property(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> Property | None:
    """Return the owner's non-deleted property, or None if inaccessible."""
    return db.scalar(
        select(Property).where(
            Property.id == property_id,
            Property.owner_id == owner.id,
            Property.deleted_at.is_(None),
        )
    )


def require_manageable_property(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> Property:
    property_obj = get_manageable_property(db, owner, property_id)
    if property_obj is None:
        raise NotFoundError("Property not found.")
    return property_obj


def require_active_property(property_obj: Property, action: str) -> None:
    if not property_obj.is_active:
        raise ConflictError(f"Cannot {action} in a hidden property.")


def require_owned_property_for_history(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> Property:
    """Read-only access that still works after a soft delete, so an owner
    never loses sight of their own financial records. Mutations keep using
    require_manageable_property."""
    property_obj = db.scalar(
        select(Property).where(
            Property.id == property_id,
            Property.owner_id == owner.id,
        )
    )
    if property_obj is None:
        raise NotFoundError("Property not found.")
    return property_obj
