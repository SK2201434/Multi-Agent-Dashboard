"""Complaints & issue tracking.

Visibility and membership (enforced in every query)
* The reporter always sees their own complaints, public or private, even
  after checking out (they must still be able to confirm or reject a
  resolution).
* Another tenant sees a PUBLIC complaint only if they are currently a
  member of the complaint's property. MEMBERSHIP RULE: the tenant has a
  stay at that property whose status is ``checked_in``. A reservation, a
  cancelled/no-show stay, a booking request or a past (checked-out) stay is
  not membership.
* PRIVATE complaints are visible only to the reporter and the property's
  owner. Lists, details, history, filters, counts and summaries all use the
  same predicate (``_tenant_visible``), so nothing can leak through a side
  door.
* The owner reaches complaints only through ``Property.owner_id``.
* Tenant and property are derived from the stay, never stored or accepted.

Lifecycle (each move writes a history row in the same transaction; the
database CHECK on the history table repeats these rules)
    (new)        --tenant creates-->           open
    open         --owner-->                    in_progress
    in_progress  --owner-->                    resolved   (awaits the reporter)
    resolved     --reporter confirms-->        closed
    resolved     --reporter rejects (note)-->  in_progress
Owners can never close; other tenants can never confirm or reject.
"""

from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.complaint import (
    COMPLAINT_STATUSES,
    Complaint,
    ComplaintStatusHistory,
)
from app.models.owner import Owner
from app.models.property import Property
from app.models.stay import Stay
from app.models.tenant import Tenant
from app.schemas.complaint import ComplaintCreate
from app.services import stay_rules as rules
from app.services.access import (
    require_manageable_property,
    require_owned_property_for_history,
)
from app.services.errors import ConflictError, ForbiddenError, NotFoundError

# (from, to) -> who may make the move
TRANSITIONS = {
    ("open", "in_progress"): "owner",
    ("in_progress", "resolved"): "owner",
    ("resolved", "closed"): "tenant",
    ("resolved", "in_progress"): "tenant",
}


def _rows():
    """Complaint + stay + property name + reporter name (all many-to-one)."""
    return (
        select(Complaint, Stay, Property.name, Tenant.full_name)
        .join(Stay, Stay.id == Complaint.stay_id)
        .join(Property, Property.id == Stay.property_id)
        .join(Tenant, Tenant.id == Stay.tenant_id)
    )


def _tenant_visible(tenant: Tenant):
    """The single visibility predicate for tenants (see module docstring)."""
    member_stay = aliased(Stay)
    is_member = exists().where(
        member_stay.tenant_id == tenant.id,
        member_stay.property_id == Stay.property_id,
        member_stay.status == "checked_in",
    )
    return or_(
        Stay.tenant_id == tenant.id,
        and_(Complaint.visibility == "public", is_member),
    )


def _load(db: Session, *filters, lock: bool = False):
    stmt = _rows().where(*filters)
    if lock:
        stmt = stmt.with_for_update(of=Complaint)
    row = db.execute(stmt).first()
    if row is None:
        raise NotFoundError("Complaint not found.")
    return tuple(row)


def _page(db: Session, filters: list, limit: int, offset: int):
    total = db.scalar(
        select(func.count(Complaint.id))
        .select_from(Complaint)
        .join(Stay, Stay.id == Complaint.stay_id)
        .join(Property, Property.id == Stay.property_id)
        .where(*filters)
    )
    rows = db.execute(
        _rows().where(*filters)
        .order_by(Complaint.created_at.desc(), Complaint.id)
        .limit(limit).offset(offset)
    ).all()
    return [tuple(r) for r in rows], total or 0


def _record(
    db: Session, complaint: Complaint, from_status: str | None, to_status: str,
    actor_type: str, actor_id: UUID, note: str | None,
) -> None:
    db.add(ComplaintStatusHistory(
        id=uuid4(), complaint_id=complaint.id, from_status=from_status,
        to_status=to_status, actor_type=actor_type,
        actor_tenant_id=actor_id if actor_type == "tenant" else None,
        actor_owner_id=actor_id if actor_type == "owner" else None,
        note=note,
    ))


def _move(
    db: Session, complaint: Complaint, to_status: str, actor_type: str,
    actor_id: UUID, note: str | None,
) -> None:
    """Validate and apply one transition. The caller commits (and holds the
    row lock), so status and history land together or not at all."""
    from_status = complaint.status
    if to_status == "closed" and actor_type == "owner":
        raise ConflictError(
            "Owners cannot close a complaint; only the tenant who raised it can "
            "confirm the resolution."
        )
    allowed_actor = TRANSITIONS.get((from_status, to_status))
    if allowed_actor is None:
        raise ConflictError(
            f"A {from_status} complaint cannot move to {to_status}."
        )
    if allowed_actor != actor_type:
        raise ForbiddenError(f"Only the {allowed_actor} can make this change.")
    complaint.status = to_status
    complaint.updated_at = rules.utc_now()
    _record(db, complaint, from_status, to_status, actor_type, actor_id, note)
    db.flush()


# ------------------------------------------------------------------ tenant


def create(db: Session, tenant: Tenant, data: ComplaintCreate):
    try:
        # The stay must be the authenticated tenant's own.
        stay = db.scalar(
            select(Stay).where(Stay.id == data.stay_id, Stay.tenant_id == tenant.id)
        )
        if stay is None:
            raise NotFoundError("Stay not found.")
        if stay.status != "checked_in":
            raise ConflictError(
                "You can raise a complaint only while you are checked in at the property."
            )
        complaint = Complaint(
            id=uuid4(), stay_id=stay.id, category=data.category, title=data.title,
            description=data.description, visibility=data.visibility, status="open",
        )
        db.add(complaint)
        db.flush()
        _record(db, complaint, None, "open", "tenant", tenant.id, None)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _load(db, Complaint.id == complaint.id)


def list_for_tenant(
    db: Session, tenant: Tenant, *, status: str | None, category: str | None,
    visibility: str | None, property_id: UUID | None, mine: bool, limit: int, offset: int,
):
    filters = [_tenant_visible(tenant)] + _filters(status, category, visibility, property_id)
    if mine:
        filters.append(Stay.tenant_id == tenant.id)
    return _page(db, filters, limit, offset)


def _filters(status, category, visibility, property_id) -> list:
    out = []
    if status:
        out.append(Complaint.status == status)
    if category:
        out.append(Complaint.category == category)
    if visibility:
        out.append(Complaint.visibility == visibility)
    if property_id:
        out.append(Stay.property_id == property_id)
    return out


def get_for_tenant(db: Session, tenant: Tenant, complaint_id: UUID):
    return _load(db, Complaint.id == complaint_id, _tenant_visible(tenant))


def history_for_tenant(db: Session, tenant: Tenant, complaint_id: UUID):
    complaint = get_for_tenant(db, tenant, complaint_id)[0]  # visibility gate
    return _history(db, complaint.id)


def summary_for_tenant(db: Session, tenant: Tenant, property_id: UUID | None):
    filters = [_tenant_visible(tenant)] + _filters(None, None, None, property_id)
    return _summary(db, filters)


def _decide(db: Session, tenant: Tenant, complaint_id: UUID, to_status: str, note):
    try:
        # Visible-or-404 first; a visible complaint raised by someone else is 403.
        complaint, stay, _name, _tenant = _load(
            db, Complaint.id == complaint_id, _tenant_visible(tenant), lock=True
        )
        if stay.tenant_id != tenant.id:
            raise ForbiddenError(
                "Only the tenant who raised this complaint can respond to its resolution."
            )
        if complaint.status != "resolved":
            raise ConflictError(
                f"Only a resolved complaint can be confirmed or rejected "
                f"(this one is {complaint.status})."
            )
        _move(db, complaint, to_status, "tenant", tenant.id, note)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _load(db, Complaint.id == complaint_id)


def confirm_resolution(db: Session, tenant: Tenant, complaint_id: UUID, note: str | None):
    return _decide(db, tenant, complaint_id, "closed", note)


def reject_resolution(db: Session, tenant: Tenant, complaint_id: UUID, note: str | None):
    return _decide(db, tenant, complaint_id, "in_progress", note)


# ------------------------------------------------------------------- owner


def list_for_owner(
    db: Session, owner: Owner, property_id: UUID, *, status: str | None,
    category: str | None, visibility: str | None, limit: int, offset: int,
):
    prop = require_owned_property_for_history(db, owner, property_id)
    filters = [Stay.property_id == prop.id, Property.owner_id == owner.id]
    filters += _filters(status, category, visibility, None)
    return _page(db, filters, limit, offset)


def get_for_owner(db: Session, owner: Owner, property_id: UUID, complaint_id: UUID):
    prop = require_owned_property_for_history(db, owner, property_id)
    return _load(
        db, Complaint.id == complaint_id, Stay.property_id == prop.id,
        Property.owner_id == owner.id,
    )


def history_for_owner(db: Session, owner: Owner, property_id: UUID, complaint_id: UUID):
    complaint = get_for_owner(db, owner, property_id, complaint_id)[0]
    return _history(db, complaint.id)


def summary_for_owner(db: Session, owner: Owner, property_id: UUID):
    prop = require_owned_property_for_history(db, owner, property_id)
    return _summary(db, [Stay.property_id == prop.id, Property.owner_id == owner.id])


def change_status(
    db: Session, owner: Owner, property_id: UUID, complaint_id: UUID,
    to_status: str, note: str | None,
):
    prop = require_manageable_property(db, owner, property_id)
    try:
        complaint = _load(
            db, Complaint.id == complaint_id, Stay.property_id == prop.id,
            Property.owner_id == owner.id, lock=True,
        )[0]
        _move(db, complaint, to_status, "owner", owner.id, note)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _load(db, Complaint.id == complaint_id)


# ------------------------------------------------------------------ shared


def _history(db: Session, complaint_id: UUID):
    return list(db.scalars(
        select(ComplaintStatusHistory)
        .where(ComplaintStatusHistory.complaint_id == complaint_id)
        .order_by(ComplaintStatusHistory.created_at, ComplaintStatusHistory.id)
    ))


def _summary(db: Session, filters: list) -> dict:
    rows = db.execute(
        select(Complaint.status, func.count(Complaint.id))
        .select_from(Complaint)
        .join(Stay, Stay.id == Complaint.stay_id)
        .join(Property, Property.id == Stay.property_id)
        .where(*filters)
        .group_by(Complaint.status)
    ).all()
    by_status = {s: 0 for s in COMPLAINT_STATUSES}
    by_status.update({status: n for status, n in rows})
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "awaiting_confirmation": by_status["resolved"],
    }
