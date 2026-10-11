"""Stay lifecycle: reserved -> checked_in -> checked_out, plus cancel/no-show.

Only owners drive the lifecycle. Every action locks the stay row first,
re-reads state under the lock, and writes timestamps exactly once.
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bed import Bed
from app.models.bed_change_request import BedChangeRequest
from app.models.owner import Owner
from app.models.property_tenant import PropertyTenant
from app.models.room import Room
from app.models.stay import Stay
from app.models.stay_bed_assignment import StayBedAssignment
from app.models.tenant import Tenant
from app.services import stay_rules as rules
from app.services.access import require_manageable_property
from app.services.errors import ConflictError, NotFoundError

STILL_OCCUPIED_MESSAGE = (
    "The bed is still physically occupied by another stay. Check that "
    "occupant out first."
)


@dataclass
class StayView:
    stay: Stay
    tenant_name: str | None = None
    bed_id: UUID | None = None
    bed_label: str | None = None
    room_number: str | None = None
    assignments: list[StayBedAssignment] = field(default_factory=list)


def _attach_beds(
    db: Session, views: list[StayView], with_history: bool
) -> None:
    """Fill bed info (current, else last held) with one query for all stays."""
    if not views:
        return
    rows = db.execute(
        select(StayBedAssignment, Bed.bed_label, Room.room_number)
        .join(Bed, Bed.id == StayBedAssignment.bed_id)
        .join(Room, Room.id == Bed.room_id)
        .where(
            StayBedAssignment.stay_id.in_([v.stay.id for v in views])
        )
        .order_by(StayBedAssignment.assigned_at, StayBedAssignment.created_at)
    ).all()
    by_stay: dict[UUID, list] = {}
    for row in rows:
        by_stay.setdefault(row[0].stay_id, []).append(row)
    for view in views:
        history = by_stay.get(view.stay.id, [])
        if history:
            last, label, room_number = history[-1]
            view.bed_id = last.bed_id
            view.bed_label = label
            view.room_number = room_number
        if with_history:
            view.assignments = [row[0] for row in history]


# ------------------------------------------------------------------ reads


def list_for_owner(
    db: Session,
    owner: Owner,
    property_id: UUID,
    status: str | None,
    tenant_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[StayView], int]:
    property_obj = require_manageable_property(db, owner, property_id)
    filters = [Stay.property_id == property_obj.id]
    if status:
        filters.append(Stay.status == status)
    if tenant_id:
        filters.append(Stay.tenant_id == tenant_id)
    total = db.scalar(select(func.count()).select_from(Stay).where(*filters))
    rows = db.execute(
        select(Stay, Tenant.full_name)
        .join(Tenant, Tenant.id == Stay.tenant_id)
        .where(*filters)
        .order_by(Stay.planned_check_in.desc(), Stay.id)
        .limit(limit)
        .offset(offset)
    ).all()
    views = [StayView(stay=s, tenant_name=name) for s, name in rows]
    _attach_beds(db, views, with_history=False)
    return views, total or 0


def get_for_owner(
    db: Session, owner: Owner, property_id: UUID, stay_id: UUID
) -> StayView:
    property_obj = require_manageable_property(db, owner, property_id)
    row = db.execute(
        select(Stay, Tenant.full_name)
        .join(Tenant, Tenant.id == Stay.tenant_id)
        .where(Stay.id == stay_id, Stay.property_id == property_obj.id)
    ).first()
    if row is None:
        raise NotFoundError("Stay not found.")
    view = StayView(stay=row[0], tenant_name=row[1])
    _attach_beds(db, [view], with_history=True)
    return view


def list_for_tenant(
    db: Session,
    tenant: Tenant,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[StayView], int]:
    filters = [Stay.tenant_id == tenant.id]
    if status:
        filters.append(Stay.status == status)
    total = db.scalar(select(func.count()).select_from(Stay).where(*filters))
    stays = db.scalars(
        select(Stay)
        .where(*filters)
        .order_by(Stay.planned_check_in.desc(), Stay.id)
        .limit(limit)
        .offset(offset)
    ).all()
    views = [StayView(stay=s) for s in stays]
    _attach_beds(db, views, with_history=False)
    return views, total or 0


def get_for_tenant(db: Session, tenant: Tenant, stay_id: UUID) -> StayView:
    stay = db.scalar(
        select(Stay).where(Stay.id == stay_id, Stay.tenant_id == tenant.id)
    )
    if stay is None:
        raise NotFoundError("Stay not found.")
    view = StayView(stay=stay)
    _attach_beds(db, [view], with_history=True)
    return view


# ---------------------------------------------------------------- actions


def _lock_stay(db: Session, property_id: UUID, stay_id: UUID) -> Stay:
    stay = db.scalar(
        select(Stay)
        .where(Stay.id == stay_id, Stay.property_id == property_id)
        .with_for_update()
    )
    if stay is None:
        raise NotFoundError("Stay not found.")
    return stay


def _current_assignment(db: Session, stay_id: UUID) -> StayBedAssignment:
    assignment = db.scalar(
        select(StayBedAssignment).where(
            StayBedAssignment.stay_id == stay_id,
            StayBedAssignment.released_at.is_(None),
        )
    )
    if assignment is None:  # invariant: every live stay has one
        raise ConflictError("The stay has no current bed assignment.")
    return assignment


def cancel_pending_bed_changes(db: Session, stay_id: UUID) -> None:
    db.execute(
        update(BedChangeRequest)
        .where(
            BedChangeRequest.stay_id == stay_id,
            BedChangeRequest.status == "pending",
        )
        .values(status="cancelled", updated_at=rules.utc_now())
    )


def _require_status(stay: Stay, expected: str, action: str) -> None:
    if stay.status != expected:
        raise ConflictError(
            f"Cannot {action}: the stay is {stay.status}, "
            f"it must be {expected}."
        )


def check_in(
    db: Session, owner: Owner, property_id: UUID, stay_id: UUID
) -> StayView:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        stay = _lock_stay(db, property_obj.id, stay_id)
        _require_status(stay, "reserved", "check in")

        if rules.business_today() < stay.planned_check_in:
            raise ConflictError(
                "Cannot check in before the planned check-in date."
            )

        associated = db.scalar(
            select(PropertyTenant.id).where(
                PropertyTenant.tenant_id == stay.tenant_id,
                PropertyTenant.property_id == property_obj.id,
                PropertyTenant.status == "active",
            )
        )
        if associated is None:
            raise ConflictError(
                "The tenant is no longer associated with this property."
            )

        # Lock order: stay -> tenant -> bed.
        db.scalar(
            select(Tenant.id).where(Tenant.id == stay.tenant_id).with_for_update()
        )
        already_here = db.scalar(
            select(Stay.id).where(
                Stay.tenant_id == stay.tenant_id,
                Stay.status == "checked_in",
                Stay.id != stay.id,
            )
        )
        if already_here is not None:
            raise ConflictError(
                "The tenant is still checked in to another stay. Check "
                "them out of it first."
            )

        assignment = _current_assignment(db, stay.id)
        ctx = rules.lock_bed_context(db, assignment.bed_id)
        if ctx is None or ctx.bed.status != "available" or not ctx.room.is_active:
            raise ConflictError("The assigned bed is not operational.")

        occupied = db.scalar(
            select(StayBedAssignment.id).where(
                StayBedAssignment.bed_id == assignment.bed_id,
                StayBedAssignment.released_at.is_(None),
                StayBedAssignment.occupied_from.is_not(None),
            )
        )
        if occupied is not None:
            raise ConflictError(STILL_OCCUPIED_MESSAGE)

        now = rules.utc_now()
        stay.status = "checked_in"
        stay.actual_check_in_at = now
        stay.checked_in_by_owner_id = owner.id
        assignment.occupied_from = now
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        rules.raise_conflict_from(exc)
    except Exception:
        db.rollback()
        raise
    return get_for_owner(db, owner, property_id, stay_id)


def check_out(
    db: Session, owner: Owner, property_id: UUID, stay_id: UUID
) -> StayView:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        stay = _lock_stay(db, property_obj.id, stay_id)
        _require_status(stay, "checked_in", "check out")

        assignment = _current_assignment(db, stay.id)
        now = rules.utc_now()
        assignment.released_at = now
        stay.status = "checked_out"
        stay.actual_check_out_at = now
        stay.checked_out_by_owner_id = owner.id
        cancel_pending_bed_changes(db, stay.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        rules.raise_conflict_from(exc)
    except Exception:
        db.rollback()
        raise
    return get_for_owner(db, owner, property_id, stay_id)


def _close_reserved(
    db: Session,
    owner: Owner,
    property_id: UUID,
    stay_id: UUID,
    new_status: str,
    reason: str | None,
) -> StayView:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        stay = _lock_stay(db, property_obj.id, stay_id)
        _require_status(
            stay,
            "reserved",
            "cancel" if new_status == "cancelled" else "mark as no-show",
        )
        if (
            new_status == "no_show"
            and rules.business_today() < stay.planned_check_in
        ):
            raise ConflictError(
                "Cannot mark a no-show before the planned check-in date."
            )

        assignment = _current_assignment(db, stay.id)
        now = rules.utc_now()
        assignment.released_at = now
        stay.status = new_status
        stay.closed_at = now
        stay.closed_by_owner_id = owner.id
        stay.close_reason = reason
        cancel_pending_bed_changes(db, stay.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        rules.raise_conflict_from(exc)
    except Exception:
        db.rollback()
        raise
    return get_for_owner(db, owner, property_id, stay_id)


def cancel(
    db: Session,
    owner: Owner,
    property_id: UUID,
    stay_id: UUID,
    reason: str | None,
) -> StayView:
    return _close_reserved(db, owner, property_id, stay_id, "cancelled", reason)


def mark_no_show(
    db: Session,
    owner: Owner,
    property_id: UUID,
    stay_id: UUID,
    reason: str | None,
) -> StayView:
    return _close_reserved(db, owner, property_id, stay_id, "no_show", reason)
