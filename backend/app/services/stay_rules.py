"""Rules shared by every Module 6 workflow.

Interval semantics (used identically by public availability, booking
validation, approval, check-in and bed changes):

* Planned dates are calendar dates forming the half-open interval
  [check_in, check_out). check_out is exclusive and may be NULL, meaning
  "no known end": it extends indefinitely.
* A stay that is physically checked in but past its planned check-out has
  not left. For availability its interval is treated as open-ended until
  the actual checkout is recorded.

Lock order, everywhere: request/stay row -> tenant row -> bed rows (by id).
Database exclusion constraints are the last line of defence; these locks
exist so the loser of a race gets a clean 409 instead of a raw DB error.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, null, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.bed import Bed
from app.models.property import Property
from app.models.room import Room
from app.models.stay import Stay
from app.models.stay_bed_assignment import StayBedAssignment
from app.services.errors import ConflictError, DomainValidationError

BED_UNAVAILABLE_MESSAGE = "The bed is not available for the requested dates."
TENANT_CONFLICT_MESSAGE = (
    "The tenant already has a reservation or stay that overlaps these dates."
)
RACE_CONFLICT_MESSAGE = (
    "The request conflicts with a concurrent change. Nothing was changed; "
    "please review the current state and try again."
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def business_today() -> date:
    """Today's calendar date in the configured business timezone."""
    zone = ZoneInfo(get_settings().business_timezone)
    return datetime.now(zone).date()


def validate_dates(check_in: date, check_out: date | None) -> None:
    if check_out is not None and check_out <= check_in:
        raise DomainValidationError(
            "planned_check_out must be after planned_check_in."
        )


def raise_conflict_from(exc: IntegrityError) -> None:
    """Translate an integrity failure into a client-safe 409."""
    raise ConflictError(RACE_CONFLICT_MESSAGE) from exc


# ------------------------------------------------------------------ beds


@dataclass
class BedContext:
    bed: Bed
    room: Room
    property: Property


def load_bed_context(
    db: Session, bed_id: UUID, lock: bool = False
) -> BedContext | None:
    statement = (
        select(Bed, Room, Property)
        .join(Room, Room.id == Bed.room_id)
        .join(Property, Property.id == Room.property_id)
        .where(Bed.id == bed_id)
    )
    if lock:
        statement = statement.with_for_update(of=Bed)
    row = db.execute(statement).first()
    return None if row is None else BedContext(*row)


def lock_bed_context(db: Session, bed_id: UUID) -> BedContext | None:
    return load_bed_context(db, bed_id, lock=True)


def bed_unbookable_reason(ctx: BedContext) -> str | None:
    """Why a bed cannot take new allocations, or None if it can."""
    if ctx.property.deleted_at is not None or not ctx.property.is_active:
        return "The property is not accepting bookings."
    if not ctx.room.is_active:
        return "The room is not accepting bookings."
    if ctx.bed.status != "available":
        return "The bed is not currently bookable."
    if ctx.room.monthly_rent_per_bed is None:
        return "No rent has been configured for this bed."
    return None


# ------------------------------------------------------- interval overlap


def _bed_blocked_condition(
    start: date, end: date | None, today: date
):
    """SQL condition: a current assignment overlaps [start, end)."""
    a = StayBedAssignment
    effective_end = case(
        (
            and_(
                a.occupied_from.is_not(None),
                a.period_end.is_not(None),
                a.period_end <= today,
            ),
            null(),
        ),
        else_=a.period_end,
    )
    conditions = [
        a.released_at.is_(None),
        or_(effective_end.is_(None), effective_end > start),
    ]
    if end is not None:
        conditions.append(a.period_start < end)
    return and_(*conditions)


def blocked_bed_ids(
    db: Session,
    bed_ids: Iterable[UUID],
    start: date,
    end: date | None,
    today: date | None = None,
) -> set[UUID]:
    """Beds (among bed_ids) held by a live stay during [start, end)."""
    ids = list(bed_ids)
    if not ids:
        return set()
    today = today or business_today()
    return set(
        db.scalars(
            select(StayBedAssignment.bed_id)
            .where(
                StayBedAssignment.bed_id.in_(ids),
                _bed_blocked_condition(start, end, today),
            )
            .distinct()
        ).all()
    )


def require_bed_free(
    db: Session, bed_id: UUID, start: date, end: date | None
) -> None:
    if blocked_bed_ids(db, [bed_id], start, end):
        raise ConflictError(BED_UNAVAILABLE_MESSAGE)


def tenant_has_conflict(
    db: Session,
    tenant_id: UUID,
    start: date,
    end: date | None,
    today: date | None = None,
) -> bool:
    """True if the tenant holds a live stay overlapping [start, end)."""
    today = today or business_today()
    effective_end = case(
        (
            and_(
                Stay.status == "checked_in",
                Stay.planned_check_out.is_not(None),
                Stay.planned_check_out <= today,
            ),
            null(),
        ),
        else_=Stay.planned_check_out,
    )
    conditions = [
        Stay.tenant_id == tenant_id,
        Stay.status.in_(("reserved", "checked_in")),
        or_(effective_end.is_(None), effective_end > start),
    ]
    if end is not None:
        conditions.append(Stay.planned_check_in < end)
    return db.scalar(select(Stay.id).where(*conditions).limit(1)) is not None


def require_tenant_free(
    db: Session,
    tenant_id: UUID,
    start: date,
    end: date | None,
) -> None:
    if tenant_has_conflict(db, tenant_id, start, end):
        raise ConflictError(TENANT_CONFLICT_MESSAGE)
