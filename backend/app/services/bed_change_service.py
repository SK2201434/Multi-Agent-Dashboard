"""Bed-change requests (same property only).

A pending request holds nothing. Approval locks request -> stay -> tenant ->
destination bed, re-validates, then releases the old assignment and inserts
a new one. The stay's rent snapshot is never touched.

Destination interval:
* reserved stay: the stay's planned dates (the move is a re-plan);
* checked-in stay: from today until the planned check-out, or open-ended if
  there is none or it has already passed (the tenant is still here).
"""

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bed_change_request import BedChangeRequest
from app.models.owner import Owner
from app.models.stay import Stay
from app.models.stay_bed_assignment import StayBedAssignment
from app.models.tenant import Tenant
from app.schemas.stay import BedChangeRequestCreate
from app.services import stay_rules as rules
from app.services.access import require_manageable_property
from app.services.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)


def _move_interval(stay: Stay, today: date) -> tuple[date, date | None]:
    if stay.status == "reserved":
        return stay.planned_check_in, stay.planned_check_out
    end = stay.planned_check_out
    if end is not None and end <= today:
        end = None
    return today, end


def _current_assignment(db: Session, stay_id: UUID) -> StayBedAssignment:
    assignment = db.scalar(
        select(StayBedAssignment).where(
            StayBedAssignment.stay_id == stay_id,
            StayBedAssignment.released_at.is_(None),
        )
    )
    if assignment is None:
        raise ConflictError("The stay has no current bed assignment.")
    return assignment


def _validate_destination(
    ctx: rules.BedContext | None, property_id: UUID
) -> rules.BedContext:
    if ctx is None:
        raise NotFoundError("Destination bed not found.")
    if ctx.property.id != property_id:
        raise DomainValidationError(
            "Bed changes are only supported within the same property."
        )
    reason = rules.bed_unbookable_reason(ctx)
    if reason:
        raise ConflictError(reason)
    return ctx


# ------------------------------------------------------------- tenant side


def create_request(
    db: Session, tenant: Tenant, data: BedChangeRequestCreate
) -> BedChangeRequest:
    try:
        stay = db.scalar(
            select(Stay)
            .where(Stay.id == data.stay_id, Stay.tenant_id == tenant.id)
            .with_for_update()
        )
        if stay is None:
            raise NotFoundError("Stay not found.")
        if stay.status not in ("reserved", "checked_in"):
            raise ConflictError(
                f"A bed change cannot be requested for a {stay.status} stay."
            )

        current = _current_assignment(db, stay.id)
        if current.bed_id == data.to_bed_id:
            raise DomainValidationError(
                "The destination is the bed you already have."
            )

        ctx = _validate_destination(
            rules.load_bed_context(db, data.to_bed_id), stay.property_id
        )

        pending = db.scalar(
            select(BedChangeRequest.id).where(
                BedChangeRequest.stay_id == stay.id,
                BedChangeRequest.status == "pending",
            )
        )
        if pending is not None:
            raise ConflictError(
                "This stay already has a pending bed-change request."
            )

        start, end = _move_interval(stay, rules.business_today())
        rules.require_bed_free(db, ctx.bed.id, start, end)

        request = BedChangeRequest(
            id=uuid4(),
            tenant_id=tenant.id,
            stay_id=stay.id,
            property_id=stay.property_id,
            from_bed_id=current.bed_id,
            to_bed_id=ctx.bed.id,
            status="pending",
            reason=data.reason,
        )
        db.add(request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        rules.raise_conflict_from(exc)
    except Exception:
        db.rollback()
        raise
    return request


def list_for_tenant(
    db: Session,
    tenant: Tenant,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[BedChangeRequest], int]:
    filters = [BedChangeRequest.tenant_id == tenant.id]
    if status:
        filters.append(BedChangeRequest.status == status)
    total = db.scalar(
        select(func.count()).select_from(BedChangeRequest).where(*filters)
    )
    items = db.scalars(
        select(BedChangeRequest)
        .where(*filters)
        .order_by(BedChangeRequest.created_at.desc(), BedChangeRequest.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), total or 0


def get_for_tenant(
    db: Session, tenant: Tenant, request_id: UUID
) -> BedChangeRequest:
    request = db.scalar(
        select(BedChangeRequest).where(
            BedChangeRequest.id == request_id,
            BedChangeRequest.tenant_id == tenant.id,
        )
    )
    if request is None:
        raise NotFoundError("Bed-change request not found.")
    return request


def cancel_by_tenant(
    db: Session, tenant: Tenant, request_id: UUID
) -> BedChangeRequest:
    try:
        request = db.scalar(
            select(BedChangeRequest)
            .where(
                BedChangeRequest.id == request_id,
                BedChangeRequest.tenant_id == tenant.id,
            )
            .with_for_update()
        )
        if request is None:
            raise NotFoundError("Bed-change request not found.")
        if request.status != "pending":
            raise ConflictError(
                f"Only pending requests can be cancelled "
                f"(this one is {request.status})."
            )
        request.status = "cancelled"
        db.commit()
    except Exception:
        db.rollback()
        raise
    return request


# -------------------------------------------------------------- owner side


def list_for_owner(
    db: Session,
    owner: Owner,
    property_id: UUID,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[BedChangeRequest, str]], int]:
    property_obj = require_manageable_property(db, owner, property_id)
    filters = [BedChangeRequest.property_id == property_obj.id]
    if status:
        filters.append(BedChangeRequest.status == status)
    total = db.scalar(
        select(func.count()).select_from(BedChangeRequest).where(*filters)
    )
    rows = db.execute(
        select(BedChangeRequest, Tenant.full_name)
        .join(Tenant, Tenant.id == BedChangeRequest.tenant_id)
        .where(*filters)
        .order_by(BedChangeRequest.created_at.desc(), BedChangeRequest.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [tuple(row) for row in rows], total or 0


def get_for_owner(
    db: Session, owner: Owner, property_id: UUID, request_id: UUID
) -> tuple[BedChangeRequest, str]:
    property_obj = require_manageable_property(db, owner, property_id)
    row = db.execute(
        select(BedChangeRequest, Tenant.full_name)
        .join(Tenant, Tenant.id == BedChangeRequest.tenant_id)
        .where(
            BedChangeRequest.id == request_id,
            BedChangeRequest.property_id == property_obj.id,
        )
    ).first()
    if row is None:
        raise NotFoundError("Bed-change request not found.")
    return tuple(row)


def _lock_pending(
    db: Session, property_id: UUID, request_id: UUID
) -> BedChangeRequest:
    request = db.scalar(
        select(BedChangeRequest)
        .where(
            BedChangeRequest.id == request_id,
            BedChangeRequest.property_id == property_id,
        )
        .with_for_update()
    )
    if request is None:
        raise NotFoundError("Bed-change request not found.")
    if request.status != "pending":
        raise ConflictError(
            f"Only pending requests can be reviewed "
            f"(this one is {request.status})."
        )
    return request


def reject(
    db: Session,
    owner: Owner,
    property_id: UUID,
    request_id: UUID,
    reason: str | None,
) -> BedChangeRequest:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        request = _lock_pending(db, property_obj.id, request_id)
        request.status = "rejected"
        request.reviewed_at = rules.utc_now()
        request.reviewed_by_owner_id = owner.id
        request.rejection_reason = reason
        db.commit()
    except Exception:
        db.rollback()
        raise
    return request


def approve(
    db: Session, owner: Owner, property_id: UUID, request_id: UUID
) -> BedChangeRequest:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        request = _lock_pending(db, property_obj.id, request_id)

        stay = db.scalar(
            select(Stay).where(Stay.id == request.stay_id).with_for_update()
        )
        if stay is None or stay.status not in ("reserved", "checked_in"):
            raise ConflictError("The stay is no longer eligible for a move.")

        db.scalar(
            select(Tenant.id).where(Tenant.id == stay.tenant_id).with_for_update()
        )

        current = _current_assignment(db, stay.id)
        if current.bed_id != request.from_bed_id:
            raise ConflictError(
                "The tenant's bed changed since this request was made."
            )

        ctx = _validate_destination(
            rules.lock_bed_context(db, request.to_bed_id), property_obj.id
        )

        today = rules.business_today()
        start, end = _move_interval(stay, today)
        rules.require_bed_free(db, ctx.bed.id, start, end)

        now = rules.utc_now()
        current.released_at = now
        db.flush()  # release the old bed before the new row is inserted

        db.add(
            StayBedAssignment(
                id=uuid4(),
                stay_id=stay.id,
                bed_id=ctx.bed.id,
                period_start=start,
                period_end=end,
                assigned_at=now,
                occupied_from=now if stay.status == "checked_in" else None,
                bed_change_request_id=request.id,
            )
        )

        request.status = "approved"
        request.reviewed_at = now
        request.reviewed_by_owner_id = owner.id
        request.effective_at = now
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        rules.raise_conflict_from(exc)
    except Exception:
        db.rollback()
        raise
    return request
