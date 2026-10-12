"""Read-only rent tracking, owner overview and reports (Module 8).

Nothing here writes. Every figure derives from the Module 7 columns:

    effective due = amount_due - amount_adjusted
    outstanding   = effective due - amount_paid

``amount_paid`` is the verified ledger net of reversals (pending/rejected
claims never touch it), so it is the single source of truth for "paid".
Collection reports use each payment's ``paid_on`` date and exclude reversed
payments (Module 7 treats a reversal as an audited correction: the payment
is void), so a past month's total can drop if a payment in it is later
reversed. Reversal dates are not reported. Collection reports sum the same ledger directly (verified payments with no
reversal) and therefore reconcile with ``amount_paid``.

Definitions
* paid           : outstanding == 0 (includes fully waived rent)
* partially_paid : outstanding > 0 and amount_paid > 0
* unpaid         : outstanding > 0 and amount_paid == 0
                   A fully waived obligation (effective due 0) is "paid" because
                   nothing is left to collect, but it is NOT cash: amount_paid
                   stays 0, every collected/collection figure sums verified
                   payments only, and rows carry ``is_fully_waived``.
* overdue        : due_date < today and outstanding > 0 (independent of the
                   payment status; an overdue row is also unpaid/partial)
* upcoming       : today <= due_date <= today + within_days (inclusive)

``today`` is the business-timezone calendar date. All aggregates run in the
database over joins that are one-to-one with an obligation (property,
tenant, current bed assignment), so rows and sums are never multiplied.
Ownership is enforced in every query through ``Property.owner_id``.
"""

from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from uuid import UUID

from sqlalchemy import (
    Select,
    and_,
    case,
    distinct,
    exists,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.bed import Bed
from app.models.owner import Owner
from app.models.property import Property
from app.models.rent import (
    PaymentClaim,
    RentObligation as O,
    RentPayment,
    RentPaymentReversal,
)
from app.models.room import Room
from app.models.stay import Stay
from app.models.stay_bed_assignment import StayBedAssignment
from app.models.tenant import Tenant
from app.schemas import dashboard as s
from app.services import rent_service as rent
from app.services import stay_rules as rules
from app.services.access import require_owned_property_for_history
from app.services.errors import DomainValidationError

MAX_WITHIN_DAYS = 365
MAX_PAGE_SIZE = 100
MAX_REPORT_DAYS = 1830  # ~5 years
ZERO = Decimal("0.00")

# ------------------------------------------------------------ SQL fragments

EFFECTIVE = O.amount_due - O.amount_adjusted
OUTSTANDING = O.amount_due - O.amount_adjusted - O.amount_paid
IS_PAID = OUTSTANDING <= 0
IS_PARTIAL = and_(OUTSTANDING > 0, O.amount_paid > 0)
IS_UNPAID = and_(OUTSTANDING > 0, O.amount_paid == 0)
STATUS_CONDITIONS = {"unpaid": IS_UNPAID, "partially_paid": IS_PARTIAL, "paid": IS_PAID}


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(ZERO)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def classify(obligation: O) -> str:
    """Payment status with the same arithmetic as the SQL conditions."""
    if rent.outstanding(obligation) <= 0:
        return "paid"
    return "partially_paid" if obligation.amount_paid > 0 else "unpaid"


def _today(today: date | None) -> date:
    return today or rules.business_today()


def _page_meta(page: int, page_size: int, total: int) -> s.PageMeta:
    return s.PageMeta(
        page=page, page_size=page_size, total_items=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


def _scope(
    owner: Owner, db: Session, property_id: UUID | None, search: str | None
) -> list:
    """Filters restricting obligations to the owner (and property/tenant).

    The query must join Property and Tenant (see ``_base``). An explicitly
    requested property that is not the owner's is a 404, not an empty list.
    """
    filters = [Property.owner_id == owner.id]
    if property_id is not None:
        require_owned_property_for_history(db, owner, property_id)
        filters.append(O.property_id == property_id)
    if search:
        filters.append(Tenant.full_name.ilike(f"%{_escape_like(search)}%", escape="\\"))
    return filters


def _base(stmt: Select) -> Select:
    return stmt.select_from(O).join(Property, Property.id == O.property_id).join(
        Tenant, Tenant.id == O.tenant_id
    )


def _counts(row, prefix: str) -> s.Counts:
    return s.Counts(
        tenant_count=getattr(row, f"{prefix}_tenants") or 0,
        obligation_count=getattr(row, f"{prefix}_obligations") or 0,
    )


def _filtered_counts(name: str, condition):
    return [
        func.count(O.id).filter(condition).label(f"{name}_obligations"),
        func.count(distinct(O.tenant_id)).filter(condition).label(f"{name}_tenants"),
    ]


def _summary(db: Session, filters: list) -> s.RentSummary:
    row = db.execute(
        _base(select(
            func.count(O.id),
            func.count(distinct(O.tenant_id)),
            func.coalesce(func.sum(O.amount_due), 0),
            func.coalesce(func.sum(EFFECTIVE), 0),
            func.coalesce(func.sum(O.amount_paid), 0),
            func.coalesce(func.sum(OUTSTANDING), 0),
        )).where(*filters)
    ).one()
    return s.RentSummary(
        obligation_count=row[0], tenant_count=row[1], total_amount=_money(row[2]),
        total_effective_due=_money(row[3]), total_paid=_money(row[4]),
        total_outstanding=_money(row[5]),
    )


def _breakdown(db: Session, filters: list) -> s.StatusBreakdown:
    columns = []
    for name, condition in STATUS_CONDITIONS.items():
        columns += _filtered_counts(name, condition)
    row = db.execute(_base(select(*columns)).where(*filters)).one()
    return s.StatusBreakdown(
        unpaid=_counts(row, "unpaid"),
        partially_paid=_counts(row, "partially_paid"),
        paid=_counts(row, "paid"),
    )


def _pending_claims(db: Session, owner: Owner, property_id, search) -> s.PendingClaims:
    stmt = (
        select(
            func.count(PaymentClaim.id),
            func.count(distinct(PaymentClaim.tenant_id)),
            func.coalesce(func.sum(PaymentClaim.amount), 0),
        )
        .select_from(PaymentClaim)
        .join(Property, Property.id == PaymentClaim.property_id)
        .join(Tenant, Tenant.id == PaymentClaim.tenant_id)
        .where(PaymentClaim.status == "pending", Property.owner_id == owner.id)
    )
    if property_id is not None:
        stmt = stmt.where(PaymentClaim.property_id == property_id)
    if search:
        stmt = stmt.where(Tenant.full_name.ilike(f"%{_escape_like(search)}%", escape="\\"))
    row = db.execute(stmt).one()
    return s.PendingClaims(
        claim_count=row[0], tenant_count=row[1], total_claimed_amount=_money(row[2])
    )


# ----------------------------------------------------------- rent tracking


def _rent_page(
    db: Session, filters: list, *, today: date, overdue: bool, page: int, page_size: int,
) -> s.RentList:
    has_pending = exists().where(
        PaymentClaim.obligation_id == O.id, PaymentClaim.status == "pending"
    )
    total = db.scalar(_base(select(func.count(O.id))).where(*filters)) or 0
    stmt = (
        _base(select(
            O, Tenant.full_name, Tenant.email, Tenant.phone, Property.name,
            Room.room_number, Bed.bed_label, has_pending.label("has_pending"),
        ))
        # At most one current assignment per stay (unique partial index), so
        # this outer join cannot multiply rows.
        .outerjoin(
            StayBedAssignment,
            and_(StayBedAssignment.stay_id == O.stay_id, StayBedAssignment.released_at.is_(None)),
        )
        .outerjoin(Bed, Bed.id == StayBedAssignment.bed_id)
        .outerjoin(Room, Room.id == Bed.room_id)
        .where(*filters)
        .order_by(O.due_date, Tenant.full_name, O.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = []
    for o, name, email, phone, prop_name, room_no, bed_label, pending in db.execute(stmt):
        items.append(s.RentRow(
            obligation_id=o.id, stay_id=o.stay_id, tenant_id=o.tenant_id,
            tenant_name=name, tenant_email=email, tenant_phone=phone,
            property_id=o.property_id, property_name=prop_name,
            room_number=room_no, bed_label=bed_label,
            period_start=o.period_start, period_end=o.period_end, due_date=o.due_date,
            amount_due=o.amount_due, amount_adjusted=o.amount_adjusted,
            effective_amount_due=rent.effective_due(o), amount_paid=o.amount_paid,
            outstanding=rent.outstanding(o), payment_status=classify(o),
            is_fully_waived=rent.effective_due(o) == 0,
            timing="overdue" if overdue else "upcoming",
            days_until_due=None if overdue else (o.due_date - today).days,
            days_overdue=(today - o.due_date).days if overdue else None,
            has_pending_claim=bool(pending),
        ))
    return s.RentList(
        summary=_summary(db, filters), status_breakdown=_breakdown(db, filters),
        items=items, pagination=_page_meta(page, page_size, total),
    )


def rent_due(
    db: Session, owner: Owner, *, within_days: int, status: str,
    property_id: UUID | None, search: str | None, page: int, page_size: int,
    overdue_page: int, overdue_page_size: int, today: date | None = None,
) -> s.RentDueResponse:
    today = _today(today)
    window_end = today + timedelta(days=within_days)
    scope = _scope(owner, db, property_id, search)
    status_filter = [] if status == "all" else [STATUS_CONDITIONS[status]]

    upcoming_filters = scope + [O.due_date >= today, O.due_date <= window_end] + status_filter
    # Overdue always needs a balance, so status=paid yields an empty list.
    overdue_filters = scope + [O.due_date < today, OUTSTANDING > 0] + status_filter

    # Headline numbers ignore the status filter so the dashboard cards stay
    # stable while the owner flips between status tabs.
    in_window = and_(O.due_date >= today, O.due_date <= window_end)
    owing = OUTSTANDING > 0
    overdue_cond = and_(O.due_date < today, owing)
    columns = (
        _filtered_counts("due_soon", and_(in_window, owing))
        + _filtered_counts("paid", and_(in_window, IS_PAID))
        + _filtered_counts("partial", and_(in_window, IS_PARTIAL))
        + _filtered_counts("unpaid", and_(in_window, IS_UNPAID))
        + _filtered_counts("overdue", overdue_cond)
        + [
            func.coalesce(func.sum(O.amount_paid), 0).label("collected"),
            func.coalesce(func.sum(OUTSTANDING), 0).label("outstanding"),
            func.coalesce(func.sum(OUTSTANDING).filter(overdue_cond), 0).label("overdue_amt"),
        ]
    )
    row = db.execute(_base(select(*columns)).where(*scope)).one()
    overview = s.RentOverview(
        due_soon=_counts(row, "due_soon"), paid=_counts(row, "paid"),
        partially_paid=_counts(row, "partial"), unpaid=_counts(row, "unpaid"),
        overdue=_counts(row, "overdue"), total_collected=_money(row.collected),
        total_outstanding=_money(row.outstanding),
        total_overdue_amount=_money(row.overdue_amt),
    )
    return s.RentDueResponse(
        filters=s.AppliedFilters(
            within_days=within_days, status=status, property_id=property_id,
            search=search, today=today, window_start=today, window_end=window_end,
            timezone=get_settings().business_timezone,
        ),
        overview=overview,
        pending_claims=_pending_claims(db, owner, property_id, search),
        upcoming=_rent_page(db, upcoming_filters, today=today, overdue=False,
                            page=page, page_size=page_size),
        overdue=_rent_page(db, overdue_filters, today=today, overdue=True,
                           page=overdue_page, page_size=overdue_page_size),
    )


# -------------------------------------------------------------- overview


def _validate_range(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise DomainValidationError("from_date must not be after to_date.")
    if (to_date - from_date).days > MAX_REPORT_DAYS:
        raise DomainValidationError("The date range may not exceed 5 years.")


def _collected(db: Session, owner: Owner, property_id, from_date, to_date) -> Decimal:
    stmt = _payments_stmt(owner, property_id, from_date, to_date, func.sum(RentPayment.amount))
    return _money(db.scalar(stmt))


def _payments_stmt(owner: Owner, property_id, from_date, to_date, *columns) -> Select:
    """Verified payments not reversed, by the payment's ``paid_on`` date."""
    stmt = (
        select(*columns)
        .select_from(RentPayment)
        .join(O, O.id == RentPayment.obligation_id)
        .join(Property, Property.id == O.property_id)
        .where(
            Property.owner_id == owner.id,
            RentPayment.paid_on >= from_date,
            RentPayment.paid_on <= to_date,
            ~exists().where(RentPaymentReversal.payment_id == RentPayment.id),
        )
    )
    if property_id is not None:
        stmt = stmt.where(O.property_id == property_id)
    return stmt


def _occupancy(db: Session, owner: Owner, property_id) -> s.OccupancyOverview:
    occupied = exists().where(
        StayBedAssignment.bed_id == Bed.id, StayBedAssignment.released_at.is_(None),
        StayBedAssignment.occupied_from.is_not(None),
        Stay.id == StayBedAssignment.stay_id, Stay.status == "checked_in",
    )
    reserved = exists().where(
        StayBedAssignment.bed_id == Bed.id, StayBedAssignment.released_at.is_(None),
        StayBedAssignment.occupied_from.is_(None),
        Stay.id == StayBedAssignment.stay_id, Stay.status == "reserved",
    )
    state = case(
        (occupied, "occupied"), (reserved, "reserved"),
        (Bed.status == "available", "vacant"), else_="unavailable",
    )
    stmt = (
        select(state, func.count(Bed.id))
        .select_from(Bed)
        .join(Room, Room.id == Bed.room_id)
        .join(Property, Property.id == Room.property_id)
        .where(Property.owner_id == owner.id, Property.deleted_at.is_(None),
               Room.is_active.is_(True))
        .group_by(state)
    )
    if property_id is not None:
        stmt = stmt.where(Room.property_id == property_id)
    counts = {name: n for name, n in db.execute(stmt)}
    total = sum(counts.values())
    occ = counts.get("occupied", 0)
    pct = (Decimal(occ) * 100 / total).quantize(Decimal("0.01")) if total else ZERO
    return s.OccupancyOverview(
        total_beds=total, occupied_beds=occ, reserved_beds=counts.get("reserved", 0),
        vacant_beds=counts.get("vacant", 0), unavailable_beds=counts.get("unavailable", 0),
        occupancy_percentage=pct,
    )


def owner_overview(
    db: Session, owner: Owner, *, property_id: UUID | None,
    from_date: date | None, to_date: date | None, today: date | None = None,
) -> s.OwnerOverviewResponse:
    today = _today(today)
    to_date = to_date or today
    from_date = from_date or to_date.replace(day=1)
    _validate_range(from_date, to_date)
    scope = _scope(owner, db, property_id, None)

    prop_filter = [Property.owner_id == owner.id, Property.deleted_at.is_(None)]
    room_stmt = (
        select(func.count(Room.id)).join(Property, Property.id == Room.property_id)
        .where(*prop_filter, Room.is_active.is_(True))
    )
    if property_id is not None:
        prop_filter.append(Property.id == property_id)
        room_stmt = room_stmt.where(Room.property_id == property_id)
    properties = db.scalar(select(func.count(Property.id)).where(*prop_filter)) or 0

    row = db.execute(
        _base(select(
            func.coalesce(func.sum(OUTSTANDING), 0),
            func.coalesce(func.sum(OUTSTANDING).filter(
                and_(O.due_date < today, OUTSTANDING > 0)), 0),
        )).where(*scope)
    ).one()
    return s.OwnerOverviewResponse(
        today=today, property_id=property_id, properties=properties,
        rooms=db.scalar(room_stmt) or 0, occupancy=_occupancy(db, owner, property_id),
        collected_from=from_date, collected_to=to_date,
        collected_in_range=_collected(db, owner, property_id, from_date, to_date),
        total_outstanding=_money(row[0]), total_overdue=_money(row[1]),
        pending_claims=_pending_claims(db, owner, property_id, None),
    )


# --------------------------------------------------------------- reports


def _default_range(from_date, to_date, today):
    to_date = to_date or today
    if from_date is None:
        index = to_date.year * 12 + to_date.month - 1 - 11
        from_date = date(index // 12, index % 12 + 1, 1)
    _validate_range(from_date, to_date)
    return from_date, to_date


def _monthly(db: Session, owner: Owner, property_id, from_date, to_date):
    if property_id is not None:
        require_owned_property_for_history(db, owner, property_id)
    month = func.date_trunc("month", RentPayment.paid_on).label("month")
    stmt = (
        _payments_stmt(owner, property_id, from_date, to_date, month,
                       func.count(RentPayment.id), func.sum(RentPayment.amount))
        .group_by(month).order_by(month)
    )
    return [(m.date(), n, _money(total)) for m, n, total in db.execute(stmt)]


def monthly_collections(
    db: Session, owner: Owner, *, property_id, from_date, to_date, today=None,
) -> s.MonthlyCollectionsReport:
    from_date, to_date = _default_range(from_date, to_date, _today(today))
    months = _monthly(db, owner, property_id, from_date, to_date)
    return s.MonthlyCollectionsReport(
        date_field="rent_payments.paid_on", from_date=from_date, to_date=to_date,
        property_id=property_id, total_collected=sum((m[2] for m in months), ZERO),
        months=[s.MonthlyCollection(month=m, payment_count=n, total_collected=t)
                for m, n, t in months],
    )


def collection_trends(
    db: Session, owner: Owner, *, property_id, from_date, to_date, today=None,
) -> s.CollectionTrendsReport:
    from_date, to_date = _default_range(from_date, to_date, _today(today))
    totals = {m: t for m, _, t in _monthly(db, owner, property_id, from_date, to_date)}
    points, previous = [], None
    cursor = from_date.replace(day=1)
    while cursor <= to_date:
        total = totals.get(cursor, ZERO)
        points.append(s.TrendPoint(
            month=cursor, total_collected=total,
            change_from_previous=None if previous is None else total - previous,
        ))
        previous = total
        cursor = rent.add_months(cursor, 1)
    return s.CollectionTrendsReport(
        date_field="rent_payments.paid_on", from_date=from_date, to_date=to_date,
        property_id=property_id, total_collected=sum(totals.values(), ZERO), points=points,
    )


def property_rent_summary(
    db: Session, owner: Owner, *, today: date | None = None
) -> s.PropertyRentSummaryReport:
    today = _today(today)
    overdue_cond = and_(O.due_date < today, OUTSTANDING > 0)
    rows = db.execute(
        select(
            Property.id, Property.name, func.count(O.id),
            func.count(distinct(O.tenant_id)),
            func.coalesce(func.sum(EFFECTIVE), 0),
            func.coalesce(func.sum(O.amount_paid), 0),
            func.coalesce(func.sum(OUTSTANDING), 0),
            func.coalesce(func.sum(OUTSTANDING).filter(overdue_cond), 0),
            func.count(O.id).filter(overdue_cond),
        )
        .select_from(Property)
        .outerjoin(O, O.property_id == Property.id)
        .where(Property.owner_id == owner.id)
        .group_by(Property.id, Property.name)
        .having(or_(Property.deleted_at.is_(None), func.count(O.id) > 0))
        .order_by(Property.name, Property.id)
    ).all()
    return s.PropertyRentSummaryReport(today=today, items=[
        s.PropertyRentSummary(
            property_id=r[0], property_name=r[1], obligation_count=r[2], tenant_count=r[3],
            total_effective_due=_money(r[4]), collected=_money(r[5]),
            outstanding=_money(r[6]), overdue=_money(r[7]), overdue_obligation_count=r[8],
        ) for r in rows
    ])


def obligation_report(
    db: Session, owner: Owner, *, status: str, property_id, search, due_from, due_to,
    page: int, page_size: int, today: date | None = None,
) -> s.ObligationReport:
    today = _today(today)
    if due_from and due_to and due_from > due_to:
        raise DomainValidationError("due_from must not be after due_to.")
    filters = _scope(owner, db, property_id, search)
    if status != "all":
        filters.append(STATUS_CONDITIONS[status])
    if due_from:
        filters.append(O.due_date >= due_from)
    if due_to:
        filters.append(O.due_date <= due_to)
    total = db.scalar(_base(select(func.count(O.id))).where(*filters)) or 0
    rows = db.execute(
        _base(select(O, Tenant.full_name, Property.name)).where(*filters)
        .order_by(O.due_date.desc(), Tenant.full_name, O.id)
        .limit(page_size).offset((page - 1) * page_size)
    ).all()
    items = []
    for o, tenant_name, property_name in rows:
        paid = classify(o) == "paid"
        items.append(s.ObligationReportRow(
            obligation_id=o.id, tenant_id=o.tenant_id, tenant_name=tenant_name,
            property_id=o.property_id, property_name=property_name,
            period_start=o.period_start, period_end=o.period_end, due_date=o.due_date,
            amount_due=o.amount_due, effective_amount_due=rent.effective_due(o),
            amount_paid=o.amount_paid, outstanding=rent.outstanding(o),
            payment_status=classify(o), is_fully_waived=rent.effective_due(o) == 0,
            timing="settled" if paid else ("overdue" if o.due_date < today else "upcoming"),
        ))
    return s.ObligationReport(
        summary=_summary(db, filters), items=items, pagination=_page_meta(page, page_size, total),
    )
