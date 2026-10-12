"""Booking requests: tenant submission and owner review.

A pending request reserves nothing. Only owner approval creates a Stay (and
its planned bed assignment), atomically, after re-validating everything
under locks.

Policy when a request is approved: the same tenant's *other* pending
requests whose dates overlap the new stay become 'expired', because they
can no longer be honoured. Other tenants' pending requests are left alone;
approving one of them fails with 409 if the bed is no longer free.

Association: a tenant's own request plus the owner's explicit approval is
mutual, verified consent, so approval creates the active property
association if none exists.
"""

from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.booking_request import BookingRequest
from app.models.owner import Owner
from app.models.property_tenant import PropertyTenant
from app.models.stay import Stay
from app.models.stay_bed_assignment import StayBedAssignment
from app.models.tenant import Tenant
from app.schemas.stay import BookingRequestCreate
from app.services import stay_rules as rules
from app.services.access import require_manageable_property
from app.services.errors import ConflictError, NotFoundError

SUPERSEDED_REASON = "Superseded by another confirmed reservation."


# ------------------------------------------------------------- tenant side


def create_request(
    db: Session, tenant: Tenant, data: BookingRequestCreate
) -> BookingRequest:
    today = rules.business_today()
    if data.planned_check_in < today:
        raise rules.DomainValidationError(
            "planned_check_in cannot be in the past."
        )
    rules.validate_dates(data.planned_check_in, data.planned_check_out)

    ctx = rules.load_bed_context(db, data.bed_id)
    # Hidden/deleted properties and inactive rooms look nonexistent.
    if (
        ctx is None
        or ctx.property.deleted_at is not None
        or not ctx.property.is_active
        or not ctx.room.is_active
    ):
        raise NotFoundError("Bed not found.")
    reason = rules.bed_unbookable_reason(ctx)
    if reason:
        raise ConflictError(reason)

    rules.require_bed_free(
        db, ctx.bed.id, data.planned_check_in, data.planned_check_out
    )
    rules.require_tenant_free(
        db, tenant.id, data.planned_check_in, data.planned_check_out
    )

    request = BookingRequest(
        id=uuid4(),
        tenant_id=tenant.id,
        bed_id=ctx.bed.id,
        property_id=ctx.property.id,
        planned_check_in=data.planned_check_in,
        planned_check_out=data.planned_check_out,
        quoted_monthly_rent=ctx.room.monthly_rent_per_bed,
        status="pending",
        tenant_message=data.message,
    )
    try:
        db.add(request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "You already have a pending request for this bed and date."
        ) from exc
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
) -> tuple[list[BookingRequest], int]:
    filters = [BookingRequest.tenant_id == tenant.id]
    if status:
        filters.append(BookingRequest.status == status)
    total = db.scalar(
        select(func.count()).select_from(BookingRequest).where(*filters)
    )
    items = db.scalars(
        select(BookingRequest)
        .where(*filters)
        .order_by(BookingRequest.created_at.desc(), BookingRequest.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), total or 0


def get_for_tenant(
    db: Session, tenant: Tenant, request_id: UUID
) -> BookingRequest:
    request = db.scalar(
        select(BookingRequest).where(
            BookingRequest.id == request_id,
            BookingRequest.tenant_id == tenant.id,
        )
    )
    if request is None:
        raise NotFoundError("Booking request not found.")
    return request


def cancel_by_tenant(
    db: Session, tenant: Tenant, request_id: UUID
) -> BookingRequest:
    try:
        request = db.scalar(
            select(BookingRequest)
            .where(
                BookingRequest.id == request_id,
                BookingRequest.tenant_id == tenant.id,
            )
            .with_for_update()
        )
        if request is None:
            raise NotFoundError("Booking request not found.")
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
) -> tuple[list[tuple[BookingRequest, Tenant, UUID | None]], int]:
    property_obj = require_manageable_property(db, owner, property_id)
    filters = [BookingRequest.property_id == property_obj.id]
    if status:
        filters.append(BookingRequest.status == status)
    total = db.scalar(
        select(func.count()).select_from(BookingRequest).where(*filters)
    )
    rows = db.execute(
        select(BookingRequest, Tenant, Stay.id)
        .join(Tenant, Tenant.id == BookingRequest.tenant_id)
        .outerjoin(Stay, Stay.booking_request_id == BookingRequest.id)
        .where(*filters)
        .order_by(BookingRequest.created_at.desc(), BookingRequest.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [tuple(row) for row in rows], total or 0


def get_for_owner(
    db: Session, owner: Owner, property_id: UUID, request_id: UUID
) -> tuple[BookingRequest, Tenant, UUID | None]:
    property_obj = require_manageable_property(db, owner, property_id)
    row = db.execute(
        select(BookingRequest, Tenant, Stay.id)
        .join(Tenant, Tenant.id == BookingRequest.tenant_id)
        .outerjoin(Stay, Stay.booking_request_id == BookingRequest.id)
        .where(
            BookingRequest.id == request_id,
            BookingRequest.property_id == property_obj.id,
        )
    ).first()
    if row is None:
        raise NotFoundError("Booking request not found.")
    return tuple(row)


def _lock_pending_request(
    db: Session, property_id: UUID, request_id: UUID
) -> BookingRequest:
    request = db.scalar(
        select(BookingRequest)
        .where(
            BookingRequest.id == request_id,
            BookingRequest.property_id == property_id,
        )
        .with_for_update()
    )
    if request is None:
        raise NotFoundError("Booking request not found.")
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
) -> BookingRequest:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        request = _lock_pending_request(db, property_obj.id, request_id)
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
) -> Stay:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        # Lock order: tenant -> request -> bed. The tenant lock comes first
        # because _expire_superseded later updates this tenant's SIBLING
        # requests; if each concurrent approval held its own request lock
        # while waiting for the tenant lock, they would deadlock. Taking the
        # tenant lock before any request row serializes same-tenant approvals
        # without anyone holding a sibling. tenant_id never changes, so the
        # unlocked read is safe; the request is re-validated under its lock.
        tenant_id = db.scalar(
            select(BookingRequest.tenant_id).where(
                BookingRequest.id == request_id,
                BookingRequest.property_id == property_obj.id,
            )
        )
        if tenant_id is None:
            raise NotFoundError("Booking request not found.")
        tenant = db.scalar(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
        request = _lock_pending_request(db, property_obj.id, request_id)
        if tenant is None or tenant.status != "active":
            raise ConflictError("The tenant account is not active.")

        ctx = rules.lock_bed_context(db, request.bed_id)
        if ctx is None or ctx.property.id != property_obj.id:
            raise ConflictError("The requested bed is no longer available.")
        reason = rules.bed_unbookable_reason(ctx)
        if reason:
            raise ConflictError(reason)

        today = rules.business_today()
        if request.planned_check_in < today:
            raise ConflictError(
                "The requested check-in date has already passed. Reject "
                "this request and ask the tenant to submit a new one."
            )

        rules.require_bed_free(
            db, ctx.bed.id, request.planned_check_in, request.planned_check_out
        )
        rules.require_tenant_free(
            db,
            tenant.id,
            request.planned_check_in,
            request.planned_check_out,
        )

        now = rules.utc_now()
        _ensure_association(db, owner, property_obj.id, tenant.id)

        stay = Stay(
            id=uuid4(),
            tenant_id=tenant.id,
            property_id=property_obj.id,
            booking_request_id=request.id,
            status="reserved",
            planned_check_in=request.planned_check_in,
            planned_check_out=request.planned_check_out,
            # The owner-controlled room rent at approval time is the
            # authoritative agreed rent; the tenant never supplies one.
            monthly_rent=ctx.room.monthly_rent_per_bed,
            created_by_owner_id=owner.id,
        )
        db.add(stay)
        db.flush()
        db.add(
            StayBedAssignment(
                id=uuid4(),
                stay_id=stay.id,
                bed_id=ctx.bed.id,
                period_start=stay.planned_check_in,
                period_end=stay.planned_check_out,
                assigned_at=now,
            )
        )

        request.status = "approved"
        request.reviewed_at = now
        request.reviewed_by_owner_id = owner.id

        _expire_superseded(db, request)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        rules.raise_conflict_from(exc)
    except Exception:
        db.rollback()
        raise
    return stay


def _ensure_association(
    db: Session, owner: Owner, property_id: UUID, tenant_id: UUID
) -> None:
    existing = db.scalar(
        select(PropertyTenant.id).where(
            PropertyTenant.tenant_id == tenant_id,
            PropertyTenant.property_id == property_id,
            PropertyTenant.status == "active",
        )
    )
    if existing is None:
        db.add(
            PropertyTenant(
                id=uuid4(),
                tenant_id=tenant_id,
                property_id=property_id,
                created_by_owner_id=owner.id,
            )
        )
        db.flush()


def _expire_superseded(db: Session, approved: BookingRequest) -> None:
    now = rules.utc_now()
    overlap = [
        BookingRequest.tenant_id == approved.tenant_id,
        BookingRequest.status == "pending",
        BookingRequest.id != approved.id,
        or_(
            BookingRequest.planned_check_out.is_(None),
            BookingRequest.planned_check_out > approved.planned_check_in,
        ),
    ]
    if approved.planned_check_out is not None:
        overlap.append(
            BookingRequest.planned_check_in < approved.planned_check_out
        )
    db.execute(
        update(BookingRequest)
        .where(and_(*overlap))
        .values(
            status="expired",
            rejection_reason=SUPERSEDED_REASON,
            updated_at=now,
        )
    )
