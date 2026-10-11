"""Rent reminders and overdue notices, from VERIFIED balances only.

The sweep recomputes everything from RentObligation.amount_paid, which only
verified payments move. Pending claims are never read here, so they cannot
suppress a reminder. Nothing is queued ahead of time, so an obligation that
is settled simply stops being selected: there is no stale unsent reminder
to cancel. Deduplication keys make the sweep safe to run any number of
times: each (recipient, obligation, reminder kind) is delivered once.

Overdue policy: exactly ONE overdue notice per recipient per obligation
(not daily), keyed on the obligation.

Windows (days until due): owner reminder when 0..7, tenant reminder when
0..2, overdue notice to both once the due date has passed.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.property import Property
from app.models.rent import RentObligation
from app.models.stay import Stay
from app.services import notification_service as notes
from app.services import rent_service
from app.services import stay_rules as rules

OWNER_WINDOW_DAYS = 7
TENANT_WINDOW_DAYS = 2


def run_sweep(db: Session, today: date | None = None) -> dict[str, int]:
    today = today or rules.business_today()
    horizon = today + timedelta(days=OWNER_WINDOW_DAYS)
    rows = db.execute(
        select(RentObligation, Property.owner_id)
        .join(Stay, Stay.id == RentObligation.stay_id)
        .join(Property, Property.id == RentObligation.property_id)
        .where(
            # effective due: waived obligations are settled too
            RentObligation.amount_paid + RentObligation.amount_adjusted
            < RentObligation.amount_due,
            RentObligation.due_date <= horizon,
            Stay.status.in_(rent_service.BILLABLE_STAY_STATUSES),
        )
        # Fixed global order: concurrent sweeps insert the same notification
        # keys in the same sequence, so they queue instead of deadlocking.
        .order_by(RentObligation.id)
    ).all()

    sent = {"owner_due_soon": 0, "tenant_due_soon": 0, "overdue": 0}
    for obligation, owner_id in rows:
        owing = f"{rent_service.outstanding(obligation):.2f}"
        days_left = (obligation.due_date - today).days
        base = dict(obligation_id=obligation.id)

        if days_left < 0:
            late = -days_left
            for kwargs, recipient in (
                ({"owner_id": owner_id}, "owner"), ({"tenant_id": obligation.tenant_id}, "tenant")
            ):
                if notes.notify(
                    db, **kwargs, kind="rent_overdue", title="Rent is overdue",
                    body=(f"Rent of {owing} due {obligation.due_date} is {late} "
                          f"day(s) overdue."),
                    dedupe_key=f"overdue:{obligation.id}", **base,
                ):
                    sent["overdue"] += 1
            continue

        if days_left <= OWNER_WINDOW_DAYS and notes.notify(
            db, owner_id=owner_id, kind="rent_due_soon_owner",
            title="Rent coming due",
            body=f"Rent of {owing} is due on {obligation.due_date} and is unpaid.",
            dedupe_key=f"owner_due_7d:{obligation.id}", **base,
        ):
            sent["owner_due_soon"] += 1
        if days_left <= TENANT_WINDOW_DAYS and notes.notify(
            db, tenant_id=obligation.tenant_id, kind="rent_due_soon_tenant",
            title="Rent due soon",
            body=f"Your rent of {owing} is due on {obligation.due_date}.",
            dedupe_key=f"tenant_due_2d:{obligation.id}", **base,
        ):
            sent["tenant_due_soon"] += 1

    db.commit()
    return sent
