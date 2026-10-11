"""Billing policy, audited adjustments, pending-claim limit, property
deletion rules and concurrent job runs."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.session import SessionLocal
from app.jobs import rent_jobs
from app.models.stay import Stay
from app.models.tenant import Tenant
from app.schemas.rent import PaymentClaimCreate
from app.services import notification_service, reminder_service, rent_service
from app.services.errors import AppError
from app.services.stay_rules import business_today
from tests.conftest import day, days
from tests.flows import TP, pp, reserve, stay_action
from tests.test_audit_edge_cases import make_late
from tests.test_payments import (
    claim,
    count_kind,
    decide,
    obligation,
    reconcile,
    setup,
)

IST = ZoneInfo("Asia/Kolkata")


def stay_obj(anchor: date, out: date | None = None) -> Stay:
    """Unsaved Stay with actual check-in/out on the given business dates."""
    at = lambda d: datetime(d.year, d.month, d.day, 10, 0, tzinfo=IST)
    return Stay(
        actual_check_in_at=at(anchor),
        actual_check_out_at=at(out) if out else None,
        planned_check_in=anchor - timedelta(days=30),  # deliberately different
        planned_check_out=anchor - timedelta(days=1),
    )


# ------------------------------------------------- cycle arithmetic (pure)


def test_cycles_anchor_on_actual_checkin_not_planned():
    cycles = rent_service.cycles_for_stay(stay_obj(date(2026, 10, 15)), date(2026, 10, 20))
    assert cycles[0] == (date(2026, 10, 15), date(2026, 11, 15))
    assert min(start for start, _ in cycles) == date(2026, 10, 15)  # nothing before occupancy


def test_month_end_anchor_recovers_and_periods_tile():
    stay = stay_obj(date(2026, 1, 31))
    cycles = rent_service.cycles_for_stay(stay, date(2026, 5, 25))
    starts = [c[0] for c in cycles]
    assert starts == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
                      date(2026, 4, 30), date(2026, 5, 31)]
    for (_, end), (next_start, _) in zip(cycles, cycles[1:]):
        assert end == next_start  # half-open periods tile exactly
    assert cycles[1] == (date(2026, 2, 28), date(2026, 3, 31))  # not Feb 28 -> Mar 28
    leap = rent_service.cycles_for_stay(stay_obj(date(2028, 1, 31)), date(2028, 2, 25))
    assert [c[0] for c in leap][:2] == [date(2028, 1, 31), date(2028, 2, 29)]


def test_checked_in_stay_keeps_billing_past_planned_checkout():
    stay = stay_obj(date(2026, 1, 15))  # planned checkout was long ago
    cycles = rent_service.cycles_for_stay(stay, date(2026, 4, 10))
    assert [c[0] for c in cycles] == [date(2026, 1, 15), date(2026, 2, 15),
                                      date(2026, 3, 15), date(2026, 4, 15)]  # Apr 15 within 7 days


def test_checkout_stops_further_cycles():
    anchor = date(2026, 10, 15)
    early = rent_service.cycles_for_stay(stay_obj(anchor, date(2026, 11, 20)), date(2027, 3, 1))
    assert [c[0] for c in early] == [date(2026, 10, 15), date(2026, 11, 15)]
    exact = rent_service.cycles_for_stay(stay_obj(anchor, date(2026, 11, 15)), date(2027, 3, 1))
    assert [c[0] for c in exact] == [date(2026, 10, 15)]  # left on the cycle day: no new month
    same_day = rent_service.cycles_for_stay(stay_obj(anchor, anchor), date(2027, 3, 1))
    assert [c[0] for c in same_day] == [date(2026, 10, 15)]  # started cycle 0 is billed in full


# ------------------------------------------------------- generation (DB)


def checked_in_stay(client, world, days_ago=0, rent="5000.00"):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=3, rent=rent)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, prop["beds"][0]["id"], days(0))
    assert stay_action(client, owner, prop, stay["id"], "check-in").status_code == 200
    return owner, prop, tenant, stay


def backdate_checkin(db, stay_id, days_ago):
    db.execute(
        text("update stays set actual_check_in_at = now() - make_interval(days => :d) where id=:s"),
        {"d": days_ago, "s": stay_id})
    db.commit()


def test_generation_starts_at_actual_checkin_and_is_idempotent(client, world, db_session):
    owner, prop, tenant, stay = checked_in_stay(client, world)
    backdate_checkin(db_session, stay["id"], 75)
    anchor = db_session.execute(
        text("select (actual_check_in_at at time zone 'Asia/Kolkata')::date from stays")).scalar()

    created = rent_service.generate_monthly_obligations(db_session)
    assert created >= 3
    rows = db_session.execute(text(
        "select period_start, period_end, due_date, "
        "lead(period_start) over (order by period_start) from rent_obligations "
        "order by period_start")).all()
    assert rows[0][0] == anchor  # no obligation before occupancy began
    assert all(r[0] == r[2] for r in rows)  # due at cycle start
    assert all(r[1] == r[3] for r in rows[:-1])  # periods tile
    assert rent_service.generate_monthly_obligations(db_session) == 0


def test_late_checkin_is_not_overdue(client, world, db_session):
    owner, prop, tenant, stay = checked_in_stay(client, world)
    # booked for 10 days ago, but only actually arrived today
    db_session.execute(text("update stays set planned_check_in = current_date - 10"))
    db_session.execute(text("update stay_bed_assignments set period_start = current_date - 10"))
    db_session.commit()
    rent_service.generate_monthly_obligations(db_session)
    assert db_session.execute(text("select count(*) from rent_obligations")).scalar() == 1
    assert db_session.execute(text("select period_start from rent_obligations")).scalar() == business_today()
    sent = reminder_service.run_sweep(db_session)
    assert sent["overdue"] == 0
    assert count_kind(db_session, "rent_overdue") == 0


def test_overstay_is_billed_until_actual_checkout_then_stops(client, world, db_session):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=2)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, prop["beds"][0]["id"], days(0), days(3))
    stay_action(client, owner, prop, stay["id"], "check-in")
    make_late(db_session, stay["id"], -70, -65)  # planned stay ended 65 days ago
    backdate_checkin(db_session, stay["id"], 70)

    created = rent_service.generate_monthly_obligations(db_session)
    planned_out = db_session.execute(text("select planned_check_out from stays")).scalar()
    after_planned = db_session.execute(
        text("select count(*) from rent_obligations where period_start >= :p"), {"p": planned_out}
    ).scalar()
    assert created >= 3 and after_planned >= 2  # overstay months are billed

    assert stay_action(client, owner, prop, stay["id"], "check-out").status_code == 200
    before = db_session.execute(text("select count(*) from rent_obligations")).scalar()
    # far in the future: nothing new once actual checkout has happened
    assert rent_service.generate_monthly_obligations(db_session, today=day(400)) == 0
    assert db_session.execute(text("select count(*) from rent_obligations")).scalar() == before


def test_checkout_catches_up_cycles_started_before_leaving_but_not_after(client, world, db_session):
    owner, prop, tenant, stay = checked_in_stay(client, world)
    backdate_checkin(db_session, stay["id"], 70)
    stay_action(client, owner, prop, stay["id"], "check-out")  # job never ran before
    rent_service.generate_monthly_obligations(db_session)
    starts = db_session.execute(text("select period_start from rent_obligations order by 1")).scalars().all()
    out_date = business_today()
    assert starts and all(s < out_date or s == starts[0] for s in starts)
    assert rent_service.generate_monthly_obligations(db_session, today=day(500)) == 0


def test_cancelled_noshow_and_reserved_stays_are_never_billed(client, world, db_session):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=3)
    a = reserve(client, owner, prop, world.tenant("a"), prop["beds"][0]["id"], days(0))
    b = reserve(client, owner, prop, world.tenant("b"), prop["beds"][1]["id"], days(0))
    reserve(client, owner, prop, world.tenant("c"), prop["beds"][2]["id"], days(0))  # stays reserved
    stay_action(client, owner, prop, a["id"], "cancel")
    stay_action(client, owner, prop, b["id"], "no-show")
    assert rent_service.generate_monthly_obligations(db_session, today=day(200)) == 0


# ----------------------------------------------------------- adjustments


def adjust(client, owner, prop, oid, amount, reason="goodwill"):
    return client.post(f"{pp(prop)}/rent-obligations/{oid}/adjustments",
                       json={"amount": amount, "reason": reason}, headers=owner["headers"])


def record(client, owner, prop, oid, amount):
    return client.post(f"{pp(prop)}/rent-obligations/{oid}/payments",
                       json={"amount": amount, "method": "cash", "paid_on": days(0)},
                       headers=owner["headers"])


def test_adjustment_preserves_original_and_updates_balances(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    res = adjust(client, owner, prop, ob["id"], "1500.00", "Moved out early")
    assert res.status_code == 201, res.text
    after = obligation(client, owner, prop, ob["id"])
    assert after["amount_due"] == "5000.00"  # original preserved
    assert after["amount_adjusted"] == "1500.00"
    assert after["effective_amount_due"] == "3500.00" and after["outstanding"] == "3500.00"
    (adj,) = after["adjustments"]
    assert adj["amount"] == "1500.00" and adj["reason"] == "Moved out early" and not adj["reversed"]
    assert record(client, owner, prop, ob["id"], "3500.00").status_code == 201
    paid = obligation(client, owner, prop, ob["id"])
    assert paid["outstanding"] == "0.00" and paid["payment_status"] == "paid"
    assert record(client, owner, prop, ob["id"], "0.01").status_code == 409
    reconcile(db_session, ob["id"])
    mine = client.get(f"{TP}/rent-obligations/{ob['id']}", headers=tenant["headers"]).json()
    assert mine["adjustments"][0]["reason"] == "Moved out early"
    note = [n for n in client.get(f"{TP}/notifications", headers=tenant["headers"]).json()["items"]
            if n["kind"] == "rent_adjusted"]
    assert len(note) == 1 and "1500.00" in note[0]["body"]


def test_full_waiver_settles_without_negative_balance(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world, due=1)
    assert adjust(client, owner, prop, ob["id"], "5000.01").status_code == 409  # over-adjustment
    assert adjust(client, owner, prop, ob["id"], "5000.00").status_code == 201
    after = obligation(client, owner, prop, ob["id"])
    assert after["effective_amount_due"] == "0.00" and after["payment_status"] == "waived"
    assert adjust(client, owner, prop, ob["id"], "0.01").status_code == 409
    assert claim(client, tenant, ob["id"], "1.00").status_code == 409
    # waived obligations are settled: no reminders, no overdue
    for offset in (-6, 0, 3):
        assert reminder_service.run_sweep(db_session, today=day(1) + timedelta(days=offset)) == {
            "owner_due_soon": 0, "tenant_due_soon": 0, "overdue": 0}


def test_adjustment_cannot_leave_payments_above_effective_due(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    assert record(client, owner, prop, ob["id"], "3000.00").status_code == 201
    res = adjust(client, owner, prop, ob["id"], "2500.00")  # outstanding is only 2000
    assert res.status_code == 409 and "Credits and refunds are not supported" in res.json()["detail"]
    assert adjust(client, owner, prop, ob["id"], "2000.00").status_code == 201
    assert obligation(client, owner, prop, ob["id"])["payment_status"] == "paid"
    # the database refuses it even if a code path forgot
    with pytest.raises(IntegrityError):
        db_session.execute(text("update rent_obligations set amount_adjusted = amount_adjusted + 1"))
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(text("update rent_obligations set amount_adjusted = amount_due + 1"))
        db_session.commit()
    db_session.rollback()


def test_pending_claim_larger_than_post_waiver_balance_cannot_be_confirmed(client, world):
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"], "3000.00").json()["id"]
    assert adjust(client, owner, prop, ob["id"], "3000.00").status_code == 201
    res = decide(client, owner, prop, ob["id"], cid, "confirm")
    assert res.status_code == 409 and "exceeds the outstanding" in res.json()["detail"]
    assert obligation(client, owner, prop, ob["id"])["amount_paid"] == "0.00"


def test_adjustment_reversal_is_audited_and_single_use(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    aid = adjust(client, owner, prop, ob["id"], "1000.00").json()["id"]
    url = f"{pp(prop)}/rent-obligations/{ob['id']}/adjustments/{aid}/reverse"
    assert client.post(url, json={"reason": ""}, headers=owner["headers"]).status_code == 422
    assert client.post(url, json={"reason": "mistake"}, headers=tenant["headers"]).status_code == 401
    assert client.post(url, json={"reason": "mistake"}, headers=world.owner("x")["headers"]).status_code == 404
    assert client.post(url, json={"reason": "entered by mistake"}, headers=owner["headers"]).status_code == 200
    assert client.post(url, json={"reason": "again"}, headers=owner["headers"]).status_code == 409
    after = obligation(client, owner, prop, ob["id"])
    assert after["amount_adjusted"] == "0.00" and after["outstanding"] == "5000.00"
    (adj,) = after["adjustments"]  # history is kept, flagged
    assert adj["reversed"] and adj["reversal_reason"] == "entered by mistake"
    assert db_session.execute(text("select count(*) from rent_adjustments")).scalar() == 1


def test_adjustment_validation_and_authorization(client, world):
    owner, prop, tenant, stay, ob = setup(world)
    assert adjust(client, owner, prop, ob["id"], "0").status_code == 422
    assert adjust(client, owner, prop, ob["id"], "-5").status_code == 422
    assert adjust(client, owner, prop, ob["id"], "10", "").status_code == 422
    assert adjust(client, owner, prop, ob["id"], "1.234").status_code == 422
    url = f"{pp(prop)}/rent-obligations/{ob['id']}/adjustments"
    assert client.post(url, json={"amount": "10", "reason": "xx yy"}, headers=tenant["headers"]).status_code == 401
    assert client.post(url, json={"amount": "10", "reason": "xx yy"}).status_code == 401
    stranger = world.owner("x")
    assert adjust(client, stranger, prop, ob["id"], "10").status_code == 404
    prop2 = world.property(owner)
    assert adjust(client, owner, prop2, ob["id"], "10").status_code == 404
    assert obligation(client, owner, prop, ob["id"])["amount_adjusted"] == "0.00"


def test_payment_reversal_and_adjustment_interplay(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    pay = record(client, owner, prop, ob["id"], "3000.00").json()
    adjust(client, owner, prop, ob["id"], "2000.00")
    assert obligation(client, owner, prop, ob["id"])["outstanding"] == "0.00"
    res = client.post(f"{pp(prop)}/rent-obligations/{ob['id']}/payments/{pay['id']}/reverse",
                      json={"reason": "bounced"}, headers=owner["headers"])
    assert res.status_code == 200
    after = obligation(client, owner, prop, ob["id"])
    assert after["amount_paid"] == "0.00" and after["outstanding"] == "3000.00"
    reconcile(db_session, ob["id"])


# --------------------------------------------------------- pending claims


def test_one_pending_claim_then_resubmit_after_rejection(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    first = claim(client, tenant, ob["id"], "1000.00")
    assert first.status_code == 201
    dup = claim(client, tenant, ob["id"], "500.00")
    assert dup.status_code == 409 and "pending" in dup.json()["detail"]
    assert decide(client, owner, prop, ob["id"], first.json()["id"], "reject").status_code == 200
    again = claim(client, tenant, ob["id"], "1000.00")
    assert again.status_code == 201  # history kept, new claim allowed
    claims = obligation(client, owner, prop, ob["id"])["claims"]
    assert sorted(c["status"] for c in claims) == ["pending", "rejected"]
    assert decide(client, owner, prop, ob["id"], again.json()["id"], "confirm").status_code == 200
    assert claim(client, tenant, ob["id"], "500.00").status_code == 201  # after a decision, again ok
    assert obligation(client, owner, prop, ob["id"])["amount_paid"] == "1000.00"  # pending never counts


def test_database_enforces_single_pending_claim(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    claim(client, tenant, ob["id"], "100.00")
    with pytest.raises(IntegrityError) as err:
        db_session.execute(text(
            "insert into payment_claims (id, obligation_id, tenant_id, property_id, amount, method, "
            "paid_on, status) select gen_random_uuid(), obligation_id, tenant_id, property_id, amount, "
            "method, paid_on, 'pending' from payment_claims"))
        db_session.commit()
    db_session.rollback()
    assert "uq_payment_claims_one_pending" in str(err.value)


def test_concurrent_claim_submissions_create_one_pending(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)

    def submit(session):
        t = session.get(Tenant, uuid.UUID(tenant["id"]))
        return rent_service.submit_claim(
            session, t, uuid.UUID(ob["id"]),
            PaymentClaimCreate(amount=Decimal("100.00"), method="upi", paid_on=business_today()),
        ).id

    results = race([submit] * 4)
    assert sorted(str(r[0]) for r in results) == ["409", "409", "409", "ok"]
    assert db_session.execute(text("select count(*) from payment_claims")).scalar() == 1
    assert count_kind(db_session, "payment_claim_submitted") == 1
    assert obligation(client, owner, prop, ob["id"])["amount_paid"] == "0.00"


# ------------------------------------------------------- property deletion


def settle_up(client, owner, prop, tenant, stay, ob):
    assert record(client, owner, prop, ob["id"], "5000.00").status_code == 201
    stay_action(client, owner, prop, stay["id"], "check-in")
    stay_action(client, owner, prop, stay["id"], "check-out")


def test_property_delete_blocked_by_pending_claim_and_open_rent_but_not_history(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    stay_action(client, owner, prop, stay["id"], "check-in")
    stay_action(client, owner, prop, stay["id"], "check-out")  # no live stays remain
    cid = claim(client, tenant, ob["id"], "1000.00").json()["id"]

    res = client.delete(pp(prop), headers=owner["headers"])
    assert res.status_code == 409 and "pending payment claims" in res.json()["detail"]
    decide(client, owner, prop, ob["id"], cid, "reject")
    res = client.delete(pp(prop), headers=owner["headers"])
    assert res.status_code == 409 and "outstanding rent" in res.json()["detail"]

    assert record(client, owner, prop, ob["id"], "5000.00").status_code == 201
    assert client.delete(pp(prop), headers=owner["headers"]).status_code == 200

    # history stays readable for the owner (and tenant) after the soft delete
    got = client.get(f"{pp(prop)}/rent-obligations/{ob['id']}", headers=owner["headers"])
    assert got.status_code == 200
    body = got.json()
    assert len(body["payments"]) == 1 and len(body["claims"]) == 1
    assert client.get(f"{pp(prop)}/rent-obligations", headers=owner["headers"]).json()["total"] == 1
    assert client.get(f"{pp(prop)}/payment-claims", headers=owner["headers"]).json()["total"] == 1
    assert client.get(f"{TP}/rent-obligations/{ob['id']}", headers=tenant["headers"]).status_code == 200
    assert client.get(f"{pp(prop)}/rent-obligations", headers=world.owner("x")["headers"]).status_code == 404
    # mutations on a deleted property stay closed
    assert record(client, owner, prop, ob["id"], "1.00").status_code == 404
    assert db_session.execute(text("select count(*) from rent_payments")).scalar() == 1


def test_waiving_the_balance_unblocks_property_delete(client, world):
    owner, prop, tenant, stay, ob = setup(world)
    stay_action(client, owner, prop, stay["id"], "check-in")
    stay_action(client, owner, prop, stay["id"], "check-out")
    assert client.delete(pp(prop), headers=owner["headers"]).status_code == 409
    assert adjust(client, owner, prop, ob["id"], "5000.00").status_code == 201
    assert client.delete(pp(prop), headers=owner["headers"]).status_code == 200


# --------------------------------------------------------- job concurrency


def race(fns):
    barrier = threading.Barrier(len(fns))

    def run(fn):
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            return ("ok", fn(session))
        except AppError as exc:
            return (exc.status_code, exc.message)
        finally:
            session.close()

    with ThreadPoolExecutor(len(fns)) as pool:
        return [f.result(timeout=60) for f in [pool.submit(run, fn) for fn in fns]]


def seed_many_stays(client, world, db_session, n=6):
    owner = world.owner()
    prop = world.property(owner, rooms=n, beds_per_room=1)
    for i in range(n):
        tenant = world.tenant(f"t{i}")
        stay = reserve(client, owner, prop, tenant, prop["beds"][i]["id"], days(0))
        stay_action(client, owner, prop, stay["id"], "check-in")
        backdate_checkin(db_session, stay["id"], 40 + i * 9)
    return owner, prop


@pytest.mark.parametrize("round_", range(2))
def test_concurrent_job_runs_never_duplicate_or_fail(client, world, db_session, round_):
    seed_many_stays(client, world, db_session)

    def full_job(session):
        created = rent_service.generate_monthly_obligations(session)
        sent = reminder_service.run_sweep(session)
        return created, sum(sent.values())

    results = race([full_job] * 4)
    assert [r[0] for r in results] == ["ok"] * 4  # nobody crashed or deadlocked out
    total = db_session.execute(text("select count(*) from rent_obligations")).scalar()
    assert sum(r[1][0] for r in results) == total  # each period created exactly once
    assert db_session.execute(text(
        "select count(*) from (select stay_id, period_start from rent_obligations "
        "group by 1,2 having count(*) > 1) d")).scalar() == 0
    notif_total = db_session.execute(text("select count(*) from notifications")).scalar()
    assert sum(r[1][1] for r in results) == notif_total  # each notification sent exactly once
    assert db_session.execute(text(
        "select count(*) from (select coalesce(owner_id, tenant_id), dedupe_key from notifications "
        "group by 1,2 having count(*) > 1) d")).scalar() == 0
    # overdue is once per obligation per recipient
    overdue = count_kind(db_session, "rent_overdue")
    assert overdue <= 2 * total
    again = full_job(db_session)
    assert again == (0, 0)  # fully idempotent afterwards


# ---------------------------------------------------- retry / deadlock handling


def _dbapi(error_cls):
    return OperationalError("stmt", {}, error_cls("simulated"))


def test_retry_recovers_from_transient_failures_with_bounded_backoff():
    calls, sleeps = [], []

    def step(session):
        calls.append(1)
        if len(calls) < 3:
            raise _dbapi(pg_errors.DeadlockDetected)
        return "done"

    class Fake:
        def rollback(self): pass
        def close(self): pass

    out = rent_jobs.run_with_retry(step, attempts=4, base_delay=0.5,
                                   session_factory=Fake, sleep=sleeps.append)
    assert out == "done" and len(calls) == 3 and sleeps == [0.5, 1.0]


def test_retry_gives_up_and_raises_instead_of_reporting_success():
    calls = []

    def step(session):
        calls.append(1)
        raise _dbapi(pg_errors.SerializationFailure)

    class Fake:
        def rollback(self): pass
        def close(self): pass

    with pytest.raises(OperationalError):
        rent_jobs.run_with_retry(step, attempts=3, base_delay=0,
                                 session_factory=Fake, sleep=lambda s: None)
    assert len(calls) == 3  # bounded


def test_non_transient_errors_are_not_retried_or_swallowed():
    calls = []

    class Fake:
        def rollback(self): pass
        def close(self): pass

    def integrity(session):
        calls.append(1)
        raise IntegrityError("stmt", {}, pg_errors.UniqueViolation("dup"))

    with pytest.raises(IntegrityError):
        rent_jobs.run_with_retry(integrity, session_factory=Fake, sleep=lambda s: None)

    def plain(session):
        calls.append(1)
        raise ValueError("boom")

    with pytest.raises(ValueError):
        rent_jobs.run_with_retry(plain, session_factory=Fake, sleep=lambda s: None)
    assert len(calls) == 2  # one attempt each


def test_real_deadlock_is_detected_and_recovered_by_retry(client, world, db_session):
    """Two transactions take the same two notification keys in opposite
    orders. PostgreSQL aborts one with a deadlock; run_with_retry must
    recover it so both complete and each key exists exactly once."""
    owner = world.owner()
    owner_id = uuid.UUID(owner["id"])
    barrier = threading.Barrier(2)
    seen_deadlocks = []

    def make_step(first, second):
        attempts = []

        def step(session):
            attempts.append(1)
            if len(attempts) == 1:  # opposing order, synchronised: forces the deadlock
                order = (first, second)
                notification_service.notify(session, owner_id=owner_id, kind="k", title="t",
                                            body="b", dedupe_key=order[0])
                barrier.wait(timeout=10)
                try:
                    notification_service.notify(session, owner_id=owner_id, kind="k", title="t",
                                                body="b", dedupe_key=order[1])
                except OperationalError as exc:
                    seen_deadlocks.append(exc)
                    raise
            else:  # retry: same deterministic order for everybody
                for key in ("key-a", "key-b"):
                    notification_service.notify(session, owner_id=owner_id, kind="k", title="t",
                                                body="b", dedupe_key=key)
            session.commit()
            return "ok"
        return step

    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(rent_jobs.run_with_retry, make_step("key-a", "key-b"),
                        base_delay=0.05, sleep=lambda s: None),
            pool.submit(rent_jobs.run_with_retry, make_step("key-b", "key-a"),
                        base_delay=0.05, sleep=lambda s: None),
        ]
        assert [f.result(timeout=60) for f in futures] == ["ok", "ok"]
    assert len(seen_deadlocks) == 1  # PostgreSQL really did detect one
    keys = db_session.execute(text("select dedupe_key from notifications order by 1")).scalars().all()
    assert keys == ["key-a", "key-b"]
