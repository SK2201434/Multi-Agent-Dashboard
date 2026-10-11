"""Rent obligations, verified payments, tenant claims and reversals.

Money rules
* A claim is only a claim. Nothing a tenant submits changes any balance.
* Only RentPayment rows (created by an owner's confirmation or an owner's
  direct recording) count, and each one moves RentObligation.amount_paid by
  exactly its amount under a row lock. The CHECK constraint on that column
  is the database-level guard against overpayment.
* Corrections are reversals (a new audited row), never edits or deletes.
* amount_due is the ORIGINAL amount and is never edited. Owner waivers are
  append-only RentAdjustment rows; effective due = amount_due -
  amount_adjusted, and outstanding = effective due - amount_paid.

Billing policy (initial version)
* Cycles are anchored on the tenant's ACTUAL check-in date (business
  timezone), taken from Stay.actual_check_in_at.
* Cycle k covers [add_months(anchor, k), add_months(anchor, k + 1)): the
  half-open periods tile exactly, each computed from the original anchor so
  month-end anchors recover (31 Jan -> 28/29 Feb, 31 Mar, 30 Apr, 31 May).
* Full monthly rent for every started cycle, no automatic proration; the
  owner can waive part or all through an audited adjustment.
* Rent is due on the first day of its cycle.
* Cycles are generated while the stay is checked in, even past its planned
  check-out. After actual check-out only cycles that started before the
  check-out date exist. Cancelled/no-show/reserved stays are never billed
  automatically and nothing is created before occupancy began.

Lock order: claim row -> obligation row. Reversals and direct recording
lock only the obligation.
"""

from calendar import monthrange
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.owner import Owner
from app.models.rent import (
    PaymentClaim,
    RentAdjustment,
    RentAdjustmentReversal,
    RentObligation,
    RentPayment,
    RentPaymentReversal,
)
from app.models.stay import Stay
from app.models.tenant import Tenant
from app.schemas.rent import (
    ObligationCreate,
    OwnerPaymentCreate,
    PaymentClaimCreate,
)
from app.services import notification_service as notes
from app.services import stay_rules as rules
from app.services.access import (
    require_manageable_property,
    require_owned_property_for_history,
)
from app.services.errors import ConflictError, DomainValidationError, NotFoundError

BILLABLE_STAY_STATUSES = ("reserved", "checked_in", "checked_out")
PENDING_CLAIM_MESSAGE = (
    "You already have a pending payment claim for this rent. Wait for the "
    "owner decision before submitting another."
)


def add_months(value: date, months: int) -> date:
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def effective_due(obligation: RentObligation) -> Decimal:
    return obligation.amount_due - obligation.amount_adjusted


def outstanding(obligation: RentObligation) -> Decimal:
    return effective_due(obligation) - obligation.amount_paid


def settlement_status(obligation: RentObligation, today: date | None = None) -> str:
    today = today or rules.business_today()
    if effective_due(obligation) == 0:
        return "waived"
    if obligation.amount_paid >= effective_due(obligation):
        return "paid"
    if obligation.due_date < today:
        return "overdue"
    return "partially_paid" if obligation.amount_paid > 0 else "unpaid"


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


# ---------------------------------------------------------------- lookups


def _lock_obligation(
    db: Session, property_id: UUID, obligation_id: UUID
) -> RentObligation:
    obligation = db.scalar(
        select(RentObligation)
        .where(
            RentObligation.id == obligation_id,
            RentObligation.property_id == property_id,
        )
        .with_for_update()
    )
    if obligation is None:
        raise NotFoundError("Rent obligation not found.")
    return obligation


def _property_owner_id(db: Session, property_id: UUID) -> UUID:
    from app.models.property import Property

    return db.scalar(select(Property.owner_id).where(Property.id == property_id))


def _validate_paid_on(paid_on: date) -> None:
    if paid_on > rules.business_today():
        raise DomainValidationError("paid_on cannot be in the future.")


# ------------------------------------------------------------- obligations


def create_obligation(
    db: Session, owner: Owner, property_id: UUID, stay_id: UUID, data: ObligationCreate
) -> RentObligation:
    property_obj = require_manageable_property(db, owner, property_id)
    try:
        stay = db.scalar(
            select(Stay)
            .where(Stay.id == stay_id, Stay.property_id == property_obj.id)
            .with_for_update()
        )
        if stay is None:
            raise NotFoundError("Stay not found.")
        if stay.status not in BILLABLE_STAY_STATUSES:
            raise ConflictError(f"Rent cannot be billed for a {stay.status} stay.")
        amount = data.amount if data.amount is not None else stay.monthly_rent
        if amount <= 0:
            raise DomainValidationError("The rent amount must be greater than zero.")
        obligation = RentObligation(
            id=uuid4(),
            stay_id=stay.id,
            tenant_id=stay.tenant_id,
            property_id=property_obj.id,
            period_start=data.period_start,
            period_end=add_months(data.period_start, 1),
            due_date=data.due_date,
            amount_due=amount,
            created_by_owner_id=owner.id,
        )
        db.add(obligation)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "A rent obligation already exists for this stay and period."
        ) from exc
    except Exception:
        db.rollback()
        raise
    return obligation


def cycles_for_stay(stay: Stay, today: date) -> list[tuple[date, date]]:
    """Billing periods (start inclusive, end exclusive) the stay owes.

    Pure function of the stay's actual check-in/out and today. Cycle 0
    always exists (the stay began it). Later cycles exist while checked in
    (up to 7 days ahead, regardless of planned check-out) or, after
    check-out, only if they started before the check-out date.
    """
    anchor = rules.business_date(stay.actual_check_in_at)
    out_date = (
        rules.business_date(stay.actual_check_out_at)
        if stay.actual_check_out_at is not None
        else None
    )
    horizon = date.fromordinal(today.toordinal() + 7)
    cycles = []
    k = 0
    while True:
        start = add_months(anchor, k)
        if k > 0:
            if out_date is not None and start >= out_date:
                break
            if out_date is None and start > horizon:
                break
        cycles.append((start, add_months(anchor, k + 1)))
        k += 1
    return cycles


def generate_monthly_obligations(db: Session, today: date | None = None) -> int:
    """Create every missing obligation the billing policy calls for.

    Idempotent and safe to run concurrently: rows are inserted in a fixed
    (stay id, period start) order, so competing runs queue behind each
    other instead of deadlocking, and the unique constraint plus ON
    CONFLICT DO NOTHING means a period is created once. Returns the number
    this call created. Commits once at the end.
    """
    from sqlalchemy.dialects.postgresql import insert

    today = today or rules.business_today()
    stays = db.scalars(
        select(Stay)
        .where(
            Stay.status.in_(("checked_in", "checked_out")),
            Stay.actual_check_in_at.is_not(None),
            Stay.monthly_rent > 0,
        )
        .order_by(Stay.id)
    ).all()
    existing = set()
    if stays:
        existing = {
            (row[0], row[1])
            for row in db.execute(
                select(RentObligation.stay_id, RentObligation.period_start).where(
                    RentObligation.stay_id.in_([st.id for st in stays])
                )
            ).all()
        }

    created = 0
    for stay in stays:
        for start, end in cycles_for_stay(stay, today):
            if (stay.id, start) in existing:
                continue
            result = db.execute(
                insert(RentObligation)
                .values(
                    id=uuid4(),
                    stay_id=stay.id,
                    tenant_id=stay.tenant_id,
                    property_id=stay.property_id,
                    period_start=start,
                    period_end=end,
                    due_date=start,
                    amount_due=stay.monthly_rent,
                    amount_paid=Decimal("0.00"),
                    amount_adjusted=Decimal("0.00"),
                )
                .on_conflict_do_nothing(constraint="uq_rent_obligations_stay_period")
                .returning(RentObligation.id)
            )
            created += 1 if result.first() is not None else 0
    db.commit()
    return created


# ------------------------------------------------------------------ reads


def _page(db, model, filters, order, limit, offset, *extra_cols):
    total = db.scalar(select(func.count()).select_from(model).where(*filters))
    stmt = select(model, *extra_cols).where(*filters).order_by(*order).limit(limit).offset(offset)
    return db.execute(stmt).all(), total or 0


def list_obligations_for_owner(
    db: Session, owner: Owner, property_id: UUID, stay_id: UUID | None,
    only_open: bool, limit: int, offset: int,
):
    prop = require_owned_property_for_history(db, owner, property_id)
    filters = [RentObligation.property_id == prop.id]
    if stay_id:
        filters.append(RentObligation.stay_id == stay_id)
    if only_open:
        filters.append(
            RentObligation.amount_paid + RentObligation.amount_adjusted < RentObligation.amount_due
        )
    total = db.scalar(select(func.count()).select_from(RentObligation).where(*filters))
    rows = db.execute(
        select(RentObligation, Tenant.full_name)
        .join(Tenant, Tenant.id == RentObligation.tenant_id)
        .where(*filters)
        .order_by(RentObligation.due_date.desc(), RentObligation.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [tuple(r) for r in rows], total or 0


def list_obligations_for_tenant(db: Session, tenant: Tenant, limit: int, offset: int):
    filters = [RentObligation.tenant_id == tenant.id]
    total = db.scalar(select(func.count()).select_from(RentObligation).where(*filters))
    rows = db.scalars(
        select(RentObligation)
        .where(*filters)
        .order_by(RentObligation.due_date.desc(), RentObligation.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total or 0


def _detail(db: Session, obligation: RentObligation):
    payments = db.execute(
        select(RentPayment, RentPaymentReversal.reason)
        .outerjoin(RentPaymentReversal, RentPaymentReversal.payment_id == RentPayment.id)
        .where(RentPayment.obligation_id == obligation.id)
        .order_by(RentPayment.created_at, RentPayment.id)
    ).all()
    claims = db.scalars(
        select(PaymentClaim)
        .where(PaymentClaim.obligation_id == obligation.id)
        .order_by(PaymentClaim.created_at, PaymentClaim.id)
    ).all()
    adjustments = db.execute(
        select(RentAdjustment, RentAdjustmentReversal.reason)
        .outerjoin(
            RentAdjustmentReversal, RentAdjustmentReversal.adjustment_id == RentAdjustment.id
        )
        .where(RentAdjustment.obligation_id == obligation.id)
        .order_by(RentAdjustment.created_at, RentAdjustment.id)
    ).all()
    return (
        obligation,
        [tuple(p) for p in payments],
        list(claims),
        [tuple(a) for a in adjustments],
    )


def get_detail_for_owner(db: Session, owner: Owner, property_id: UUID, obligation_id: UUID):
    prop = require_owned_property_for_history(db, owner, property_id)
    obligation = db.scalar(
        select(RentObligation).where(
            RentObligation.id == obligation_id, RentObligation.property_id == prop.id
        )
    )
    if obligation is None:
        raise NotFoundError("Rent obligation not found.")
    return _detail(db, obligation)


def get_detail_for_tenant(db: Session, tenant: Tenant, obligation_id: UUID):
    obligation = db.scalar(
        select(RentObligation).where(
            RentObligation.id == obligation_id, RentObligation.tenant_id == tenant.id
        )
    )
    if obligation is None:
        raise NotFoundError("Rent obligation not found.")
    return _detail(db, obligation)


# -------------------------------------------------------- tenant: claims


def submit_claim(
    db: Session, tenant: Tenant, obligation_id: UUID, data: PaymentClaimCreate
) -> PaymentClaim:
    _validate_paid_on(data.paid_on)
    try:
        obligation = db.scalar(
            select(RentObligation).where(
                RentObligation.id == obligation_id,
                RentObligation.tenant_id == tenant.id,
            )
        )
        if obligation is None:
            raise NotFoundError("Rent obligation not found.")
        stay_status = db.scalar(select(Stay.status).where(Stay.id == obligation.stay_id))
        if stay_status not in BILLABLE_STAY_STATUSES:
            raise ConflictError("Payments cannot be claimed for this stay.")
        # Early, advisory check against VERIFIED balance only. The binding
        # check happens at confirmation under the obligation lock.
        if data.amount > outstanding(obligation):
            raise ConflictError(
                f"The amount exceeds the outstanding balance of "
                f"{_money(outstanding(obligation))}."
            )

        claim = PaymentClaim(
            id=uuid4(),
            obligation_id=obligation.id,
            tenant_id=tenant.id,
            property_id=obligation.property_id,
            amount=data.amount,
            method=data.method,
            paid_on=data.paid_on,
            reference=data.reference,
            status="pending",
        )
        if db.scalar(
            select(PaymentClaim.id).where(
                PaymentClaim.obligation_id == obligation.id,
                PaymentClaim.tenant_id == tenant.id,
                PaymentClaim.status == "pending",
            )
        ):
            raise ConflictError(PENDING_CLAIM_MESSAGE)
        db.add(claim)
        db.flush()
        notes.notify(
            db,
            owner_id=_property_owner_id(db, obligation.property_id),
            kind="payment_claim_submitted",
            title="Payment claim awaiting your review",
            body=(
                f"{tenant.full_name} says they paid {_money(claim.amount)} "
                f"via {claim.method} for rent due {obligation.due_date}. "
                f"Confirm or reject it once you have verified the payment."
            ),
            dedupe_key=f"claim_submitted:{claim.id}",
            obligation_id=obligation.id,
            claim_id=claim.id,
        )
        db.commit()
    except IntegrityError as exc:  # concurrent duplicate pending claim
        db.rollback()
        raise ConflictError(PENDING_CLAIM_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise
    return claim


def list_claims_for_tenant(db: Session, tenant: Tenant, status: str | None, limit: int, offset: int):
    filters = [PaymentClaim.tenant_id == tenant.id]
    if status:
        filters.append(PaymentClaim.status == status)
    total = db.scalar(select(func.count()).select_from(PaymentClaim).where(*filters))
    rows = db.scalars(
        select(PaymentClaim).where(*filters)
        .order_by(PaymentClaim.created_at.desc(), PaymentClaim.id)
        .limit(limit).offset(offset)
    ).all()
    return list(rows), total or 0


def get_claim_for_tenant(db: Session, tenant: Tenant, claim_id: UUID) -> PaymentClaim:
    claim = db.scalar(
        select(PaymentClaim).where(
            PaymentClaim.id == claim_id, PaymentClaim.tenant_id == tenant.id
        )
    )
    if claim is None:
        raise NotFoundError("Payment claim not found.")
    return claim


def list_claims_for_owner(
    db: Session, owner: Owner, property_id: UUID, status: str | None, limit: int, offset: int
):
    prop = require_owned_property_for_history(db, owner, property_id)
    filters = [PaymentClaim.property_id == prop.id]
    if status:
        filters.append(PaymentClaim.status == status)
    total = db.scalar(select(func.count()).select_from(PaymentClaim).where(*filters))
    rows = db.execute(
        select(PaymentClaim, Tenant.full_name)
        .join(Tenant, Tenant.id == PaymentClaim.tenant_id)
        .where(*filters)
        .order_by(PaymentClaim.created_at.desc(), PaymentClaim.id)
        .limit(limit).offset(offset)
    ).all()
    return [tuple(r) for r in rows], total or 0


# ---------------------------------------------------- owner: decisions


def _lock_pending_claim(
    db: Session, property_id: UUID, obligation_id: UUID, claim_id: UUID
) -> PaymentClaim:
    claim = db.scalar(
        select(PaymentClaim)
        .where(
            PaymentClaim.id == claim_id,
            PaymentClaim.obligation_id == obligation_id,
            PaymentClaim.property_id == property_id,
        )
        .with_for_update()
    )
    if claim is None:
        raise NotFoundError("Payment claim not found.")
    if claim.status != "pending":
        raise ConflictError(
            f"This claim was already {claim.status}; a decision cannot be changed. "
            "Use a payment reversal to correct a confirmed payment."
        )
    return claim


def _apply_payment(
    db: Session, obligation: RentObligation, owner: Owner, *, amount: Decimal,
    method: str, paid_on: date, reference: str | None, claim_id: UUID | None,
) -> RentPayment:
    if amount > outstanding(obligation):
        raise ConflictError(
            f"The amount {_money(amount)} exceeds the outstanding balance of "
            f"{_money(outstanding(obligation))}. Nothing was recorded; reject the "
            "claim, or settle the discrepancy explicitly (e.g. record the "
            "correct amount yourself)."
        )
    payment = RentPayment(
        id=uuid4(),
        obligation_id=obligation.id,
        claim_id=claim_id,
        source="claim" if claim_id else "owner_recorded",
        amount=amount,
        method=method,
        paid_on=paid_on,
        reference=reference,
        recorded_by_owner_id=owner.id,
    )
    db.add(payment)
    obligation.amount_paid = obligation.amount_paid + amount
    db.flush()
    return payment


def confirm_claim(
    db: Session, owner: Owner, property_id: UUID, obligation_id: UUID, claim_id: UUID
) -> PaymentClaim:
    prop = require_manageable_property(db, owner, property_id)
    try:
        claim = _lock_pending_claim(db, prop.id, obligation_id, claim_id)
        obligation = _lock_obligation(db, prop.id, obligation_id)  # recomputed under lock

        _apply_payment(
            db, obligation, owner, amount=claim.amount, method=claim.method,
            paid_on=claim.paid_on, reference=claim.reference, claim_id=claim.id,
        )
        claim.status = "confirmed"
        claim.decided_at = rules.utc_now()
        claim.decided_by_owner_id = owner.id

        settled = outstanding(obligation) == 0
        notes.notify(
            db, tenant_id=claim.tenant_id, kind="payment_claim_confirmed",
            title="Your payment was confirmed",
            body=(
                f"Your payment of {_money(claim.amount)} for rent due "
                f"{obligation.due_date} was confirmed."
                + (" The rent is now fully paid." if settled
                   else f" {_money(outstanding(obligation))} is still outstanding.")
            ),
            dedupe_key=f"claim_decided:{claim.id}",
            obligation_id=obligation.id, claim_id=claim.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "The payment could not be recorded because of a concurrent change."
        ) from exc
    except Exception:
        db.rollback()
        raise
    return claim


def reject_claim(
    db: Session, owner: Owner, property_id: UUID, obligation_id: UUID,
    claim_id: UUID, reason: str | None,
) -> PaymentClaim:
    prop = require_manageable_property(db, owner, property_id)
    try:
        claim = _lock_pending_claim(db, prop.id, obligation_id, claim_id)
        claim.status = "rejected"
        claim.decided_at = rules.utc_now()
        claim.decided_by_owner_id = owner.id
        claim.rejection_reason = reason
        due = db.scalar(select(RentObligation.due_date).where(RentObligation.id == obligation_id))
        notes.notify(
            db, tenant_id=claim.tenant_id, kind="payment_claim_rejected",
            title="Your payment claim was rejected",
            body=(
                f"Your claim of {_money(claim.amount)} for rent due {due} was "
                f"rejected." + (f" Reason: {reason}" if reason else "")
                + " The rent is still outstanding."
            ),
            dedupe_key=f"claim_decided:{claim.id}",
            obligation_id=obligation_id, claim_id=claim.id,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return claim


def record_owner_payment(
    db: Session, owner: Owner, property_id: UUID, obligation_id: UUID, data: OwnerPaymentCreate
) -> RentPayment:
    prop = require_manageable_property(db, owner, property_id)
    _validate_paid_on(data.paid_on)
    try:
        obligation = _lock_obligation(db, prop.id, obligation_id)
        payment = _apply_payment(
            db, obligation, owner, amount=data.amount, method=data.method,
            paid_on=data.paid_on, reference=data.reference, claim_id=None,
        )
        notes.notify(
            db, tenant_id=obligation.tenant_id, kind="payment_recorded",
            title="A payment was recorded",
            body=(f"The owner recorded a payment of {_money(data.amount)} for rent "
                  f"due {obligation.due_date}."),
            dedupe_key=f"payment_recorded:{payment.id}", obligation_id=obligation.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("The payment conflicts with a concurrent change.") from exc
    except Exception:
        db.rollback()
        raise
    return payment


def reverse_payment(
    db: Session, owner: Owner, property_id: UUID, obligation_id: UUID,
    payment_id: UUID, reason: str,
) -> RentPayment:
    prop = require_manageable_property(db, owner, property_id)
    try:
        obligation = _lock_obligation(db, prop.id, obligation_id)
        payment = db.scalar(
            select(RentPayment).where(
                RentPayment.id == payment_id, RentPayment.obligation_id == obligation.id
            )
        )
        if payment is None:
            raise NotFoundError("Payment not found.")
        if db.scalar(select(RentPaymentReversal.id).where(RentPaymentReversal.payment_id == payment.id)):
            raise ConflictError("This payment has already been reversed.")

        db.add(RentPaymentReversal(
            id=uuid4(), payment_id=payment.id, reason=reason.strip(),
            reversed_by_owner_id=owner.id,
        ))
        obligation.amount_paid = obligation.amount_paid - payment.amount
        db.flush()
        notes.notify(
            db, tenant_id=obligation.tenant_id, kind="payment_reversed",
            title="A payment was reversed",
            body=(f"A recorded payment of {_money(payment.amount)} for rent due "
                  f"{obligation.due_date} was reversed. Reason: {reason.strip()}"),
            dedupe_key=f"payment_reversed:{payment.id}", obligation_id=obligation.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("This payment has already been reversed.") from exc
    except Exception:
        db.rollback()
        raise
    return payment


# ------------------------------------------------------------- adjustments


def adjust_obligation(
    db: Session, owner: Owner, property_id: UUID, obligation_id: UUID,
    amount: Decimal, reason: str,
) -> RentAdjustment:
    """Waive/reduce part of an obligation (audited, append-only).

    Only the still-outstanding part can be waived, so verified payments can
    never end up exceeding the effective amount due (credits/refunds are
    out of scope: reverse the payment instead).
    """
    prop = require_manageable_property(db, owner, property_id)
    try:
        obligation = _lock_obligation(db, prop.id, obligation_id)
        remaining = outstanding(obligation)
        if amount > remaining:
            raise ConflictError(
                f"A reduction of {_money(amount)} would exceed the outstanding "
                f"balance of {_money(remaining)}; verified payments already cover "
                "the rest. Credits and refunds are not supported - reverse the "
                "payment first if it was recorded in error."
            )
        adjustment = RentAdjustment(
            id=uuid4(), obligation_id=obligation.id, amount=amount,
            reason=reason.strip(), created_by_owner_id=owner.id,
        )
        db.add(adjustment)
        obligation.amount_adjusted = obligation.amount_adjusted + amount
        db.flush()
        notes.notify(
            db, tenant_id=obligation.tenant_id, kind="rent_adjusted",
            title="Your rent was adjusted",
            body=(f"The owner reduced your rent due {obligation.due_date} by "
                  f"{_money(amount)}. Reason: {reason.strip()} Outstanding is now "
                  f"{_money(outstanding(obligation))}."),
            dedupe_key=f"rent_adjusted:{adjustment.id}", obligation_id=obligation.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("The adjustment conflicts with a concurrent change.") from exc
    except Exception:
        db.rollback()
        raise
    return adjustment


def reverse_adjustment(
    db: Session, owner: Owner, property_id: UUID, obligation_id: UUID,
    adjustment_id: UUID, reason: str,
) -> RentAdjustment:
    prop = require_manageable_property(db, owner, property_id)
    try:
        obligation = _lock_obligation(db, prop.id, obligation_id)
        adjustment = db.scalar(
            select(RentAdjustment).where(
                RentAdjustment.id == adjustment_id,
                RentAdjustment.obligation_id == obligation.id,
            )
        )
        if adjustment is None:
            raise NotFoundError("Adjustment not found.")
        if db.scalar(select(RentAdjustmentReversal.id).where(
                RentAdjustmentReversal.adjustment_id == adjustment.id)):
            raise ConflictError("This adjustment has already been reversed.")
        db.add(RentAdjustmentReversal(
            id=uuid4(), adjustment_id=adjustment.id, reason=reason.strip(),
            reversed_by_owner_id=owner.id,
        ))
        obligation.amount_adjusted = obligation.amount_adjusted - adjustment.amount
        db.flush()
        notes.notify(
            db, tenant_id=obligation.tenant_id, kind="rent_adjustment_reversed",
            title="A rent reduction was withdrawn",
            body=(f"A reduction of {_money(adjustment.amount)} on rent due "
                  f"{obligation.due_date} was reversed. Reason: {reason.strip()}"),
            dedupe_key=f"rent_adjustment_reversed:{adjustment.id}",
            obligation_id=obligation.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("This adjustment has already been reversed.") from exc
    except Exception:
        db.rollback()
        raise
    return adjustment
