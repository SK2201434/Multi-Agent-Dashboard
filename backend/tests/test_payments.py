import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.owner import Owner
from app.services import reminder_service, rent_service
from app.services.errors import AppError
from app.services.stay_rules import business_today
from tests.conftest import day, days
from tests.flows import TP, pp, reserve, stay_action

PAID_ON = None


def setup(world, rent="5000.00", due=5):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=2, rent=rent)
    tenant = world.tenant()
    stay = reserve(world.client, owner, prop, tenant, prop["beds"][0]["id"], days(0))
    res = world.client.post(
        f"{pp(prop)}/stays/{stay['id']}/rent-obligations",
        json={"period_start": days(0), "due_date": days(due)},
        headers=owner["headers"],
    )
    assert res.status_code == 201, res.text
    return owner, prop, tenant, stay, res.json()


def claim(client, tenant, obligation_id, amount="5000.00", method="upi", ref="UTR1"):
    return client.post(
        f"{TP}/rent-obligations/{obligation_id}/payment-claims",
        json={"amount": amount, "method": method, "paid_on": days(0), "reference": ref},
        headers=tenant["headers"],
    )


def decide(client, owner, prop, obligation_id, claim_id, action, body=None):
    return client.post(
        f"{pp(prop)}/rent-obligations/{obligation_id}/payment-claims/{claim_id}/{action}",
        json=body or {}, headers=owner["headers"],
    )


def obligation(client, owner, prop, oid):
    res = client.get(f"{pp(prop)}/rent-obligations/{oid}", headers=owner["headers"])
    assert res.status_code == 200, res.text
    return res.json()


def reconcile(db, oid):
    """amount_paid must equal the verified ledger net of reversals."""
    row = db.execute(text(
        "select o.amount_paid, coalesce(sum(p.amount) filter (where r.id is null), 0) "
        "from rent_obligations o left join rent_payments p on p.obligation_id=o.id "
        "left join rent_payment_reversals r on r.payment_id=p.id "
        "where o.id=:i group by o.id"), {"i": oid}).one()
    assert row[0] == row[1]


def notifs(client, who):
    return client.get("/api/v1/notifications" if who.get("owner") else f"{TP}/notifications",
                      headers=who["headers"]).json()["items"]


# ------------------------------------------------------- claims never count


def test_pending_claim_changes_nothing(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    assert ob["outstanding"] == "5000.00" and ob["payment_status"] == "unpaid"
    res = claim(client, tenant, ob["id"], "2000.00")
    assert res.status_code == 201 and res.json()["status"] == "pending"
    after = obligation(client, owner, prop, ob["id"])
    assert after["amount_paid"] == "0.00" and after["outstanding"] == "5000.00"
    assert after["payment_status"] == "unpaid" and after["payments"] == []
    mine = client.get(f"{TP}/rent-obligations/{ob['id']}", headers=tenant["headers"]).json()
    assert mine["outstanding"] == "5000.00"
    assert db_session.execute(text("select count(*) from rent_payments")).scalar() == 0


def test_rejected_claim_changes_nothing(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"]).json()["id"]
    res = decide(client, owner, prop, ob["id"], cid, "reject", {"reason": "No such transfer"})
    assert res.status_code == 200 and res.json()["status"] == "rejected"
    assert res.json()["rejection_reason"] == "No such transfer"
    after = obligation(client, owner, prop, ob["id"])
    assert after["amount_paid"] == "0.00" and after["outstanding"] == "5000.00"
    assert db_session.execute(text("select count(*) from rent_payments")).scalar() == 0
    assert decide(client, owner, prop, ob["id"], cid, "confirm").status_code == 409


def test_confirmed_claim_updates_balance_exactly_once_and_partial(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"], "2000.00", "bank_transfer", "REF9").json()["id"]
    res = decide(client, owner, prop, ob["id"], cid, "confirm")
    assert res.status_code == 200 and res.json()["status"] == "confirmed"
    after = obligation(client, owner, prop, ob["id"])
    assert after["amount_paid"] == "2000.00" and after["outstanding"] == "3000.00"
    assert after["payment_status"] == "partially_paid"
    (pay,) = after["payments"]
    assert pay["source"] == "claim" and pay["claim_id"] == cid and pay["reference"] == "REF9"

    for _ in range(2):  # repeats are refused and do not double count
        assert decide(client, owner, prop, ob["id"], cid, "confirm").status_code == 409
    assert decide(client, owner, prop, ob["id"], cid, "reject").status_code == 409
    again = obligation(client, owner, prop, ob["id"])
    assert again["amount_paid"] == "2000.00" and len(again["payments"]) == 1
    reconcile(db_session, ob["id"])


def test_full_confirmation_settles(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"], "5000.00").json()["id"]
    decide(client, owner, prop, ob["id"], cid, "confirm")
    after = obligation(client, owner, prop, ob["id"])
    assert after["outstanding"] == "0.00" and after["payment_status"] == "paid"
    assert claim(client, tenant, ob["id"], "1.00").status_code == 409


def test_claim_over_balance_rejected_at_submit_and_confirm(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    assert claim(client, tenant, ob["id"], "5000.01").status_code == 409
    c1 = claim(client, tenant, ob["id"], "3000.00").json()["id"]
    # the balance moves while the claim is pending: the owner records cash
    res = client.post(f"{pp(prop)}/rent-obligations/{ob['id']}/payments",
                      json={"amount": "3000.00", "method": "cash", "paid_on": days(0)},
                      headers=owner["headers"])
    assert res.status_code == 201 and res.json()["source"] == "owner_recorded"
    res = decide(client, owner, prop, ob["id"], c1, "confirm")
    assert res.status_code == 409 and "exceeds the outstanding" in res.json()["detail"]
    after = obligation(client, owner, prop, ob["id"])
    assert after["amount_paid"] == "3000.00"
    assert {c["id"]: c["status"] for c in after["claims"]}[c1] == "pending"  # owner must resolve
    # explicit resolution: reject it, tenant claims the true remainder, owner confirms
    assert decide(client, owner, prop, ob["id"], c1, "reject").status_code == 200
    c2 = claim(client, tenant, ob["id"], "2000.00").json()["id"]
    assert decide(client, owner, prop, ob["id"], c2, "confirm").status_code == 200
    final = obligation(client, owner, prop, ob["id"])
    assert final["outstanding"] == "0.00"
    reconcile(db_session, ob["id"])


def test_input_validation_and_spoofing(client, world):
    owner, prop, tenant, stay, ob = setup(world)
    base = f"{TP}/rent-obligations/{ob['id']}/payment-claims"
    good = {"amount": "10.00", "method": "upi", "paid_on": days(0)}
    for bad in ({**good, "amount": "0"}, {**good, "amount": "-5"}, {**good, "method": "bitcoin"},
                {**good, "status": "confirmed"}, {**good, "amount_paid": "5000"},
                {**good, "tenant_id": str(uuid.uuid4())}, {**good, "amount": "1.234"}):
        assert client.post(base, json=bad, headers=tenant["headers"]).status_code == 422, bad
    future = {**good, "paid_on": days(3)}
    assert client.post(base, json=future, headers=tenant["headers"]).status_code == 422


# ------------------------------------------------------------ authorization


def test_authorization_on_claims(client, world):
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"]).json()["id"]
    stranger, other_tenant = world.owner("x"), world.tenant("o")
    url = f"{pp(prop)}/rent-obligations/{ob['id']}/payment-claims/{cid}"
    # a tenant (even the claimant) cannot decide
    for action in ("confirm", "reject"):
        assert client.post(f"{url}/{action}", headers=tenant["headers"]).status_code == 401
        assert client.post(f"{url}/{action}").status_code == 401
        assert client.post(f"{url}/{action}", headers=stranger["headers"]).status_code == 404
    # claim must belong to the obligation named in the URL / another property
    prop2 = world.property(owner)
    assert decide(client, owner, prop2, ob["id"], cid, "confirm").status_code == 404
    assert decide(client, owner, prop, str(uuid.uuid4()), cid, "confirm").status_code == 404
    # other tenant cannot see/claim against it
    assert client.get(f"{TP}/rent-obligations/{ob['id']}", headers=other_tenant["headers"]).status_code == 404
    assert claim(client, other_tenant, ob["id"]).status_code == 404
    assert client.get(f"{TP}/payment-claims/{cid}", headers=other_tenant["headers"]).status_code == 404
    assert client.get(f"{pp(prop)}/rent-obligations", headers=stranger["headers"]).status_code == 404
    assert client.get(f"{pp(prop)}/payment-claims", headers=stranger["headers"]).status_code == 404
    # owner endpoints reject tenant tokens
    assert client.get(f"{pp(prop)}/payment-claims", headers=tenant["headers"]).status_code == 401
    # nothing was changed by any of it
    assert obligation(client, owner, prop, ob["id"])["amount_paid"] == "0.00"


# -------------------------------------------------- owner payments / reversal


def test_owner_recorded_payment_and_audited_reversal(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    url = f"{pp(prop)}/rent-obligations/{ob['id']}/payments"
    body = {"amount": "1500.00", "method": "cash", "paid_on": days(0)}
    assert client.post(url, json={**body, "amount": "5000.01"}, headers=owner["headers"]).status_code == 409
    assert client.post(url, json=body, headers=tenant["headers"]).status_code == 401
    pay = client.post(url, json=body, headers=owner["headers"]).json()
    assert obligation(client, owner, prop, ob["id"])["outstanding"] == "3500.00"

    rev = f"{url}/{pay['id']}/reverse"
    assert client.post(rev, json={"reason": ""}, headers=owner["headers"]).status_code == 422
    assert client.post(rev, json={"reason": "typo"}, headers=tenant["headers"]).status_code == 401
    assert client.post(rev, json={"reason": "entered twice"}, headers=owner["headers"]).status_code == 200
    assert client.post(rev, json={"reason": "again"}, headers=owner["headers"]).status_code == 409
    after = obligation(client, owner, prop, ob["id"])
    assert after["outstanding"] == "5000.00"
    (kept,) = after["payments"]  # history is kept, flagged
    assert kept["reversed"] is True and kept["reversal_reason"] == "entered twice"
    reconcile(db_session, ob["id"])


def test_reversing_a_confirmed_claim_payment_keeps_claim_history(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"], "5000.00").json()["id"]
    decide(client, owner, prop, ob["id"], cid, "confirm")
    pay = obligation(client, owner, prop, ob["id"])["payments"][0]
    res = client.post(f"{pp(prop)}/rent-obligations/{ob['id']}/payments/{pay['id']}/reverse",
                      json={"reason": "bounced cheque"}, headers=owner["headers"])
    assert res.status_code == 200
    after = obligation(client, owner, prop, ob["id"])
    assert after["outstanding"] == "5000.00"
    assert after["claims"][0]["status"] == "confirmed"
    # the claim cannot be confirmed a second time to "re-pay"
    assert decide(client, owner, prop, ob["id"], cid, "confirm").status_code == 409


def test_database_refuses_overpayment_and_duplicate_claim_payment(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    with pytest.raises(IntegrityError):
        db_session.execute(text("update rent_obligations set amount_paid = amount_due + 1"))
        db_session.commit()
    db_session.rollback()
    cid = claim(client, tenant, ob["id"], "100.00").json()["id"]
    decide(client, owner, prop, ob["id"], cid, "confirm")
    with pytest.raises(IntegrityError):
        db_session.execute(text(
            "insert into rent_payments (id, obligation_id, claim_id, source, amount, method, paid_on, "
            "recorded_by_owner_id) select gen_random_uuid(), obligation_id, claim_id, source, amount, "
            "method, paid_on, recorded_by_owner_id from rent_payments"))
        db_session.commit()
    db_session.rollback()


# --------------------------------------------------------------- concurrency


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


def confirmer(owner_id, prop_id, oid, cid):
    def call(session):
        owner = session.get(Owner, uuid.UUID(owner_id))
        return rent_service.confirm_claim(
            session, owner, uuid.UUID(prop_id), uuid.UUID(oid), uuid.UUID(cid))
    return call


def paying(owner_id, prop_id, oid, amount):
    from app.schemas.rent import OwnerPaymentCreate

    def call(session):
        owner = session.get(Owner, uuid.UUID(owner_id))
        return rent_service.record_owner_payment(
            session, owner, uuid.UUID(prop_id), uuid.UUID(oid),
            OwnerPaymentCreate(amount=amount, method="cash", paid_on=business_today()),
        ).id
    return call


@pytest.mark.parametrize("round_", range(3))
def test_concurrent_confirmation_and_cash_payments_cannot_overpay(client, world, db_session, round_):
    """A pending 3000 claim races two owner-recorded 3000 payments on a 5000
    obligation: whichever lands first wins, the others must be refused."""
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"], "3000.00").json()["id"]
    results = race([
        confirmer(owner["id"], prop["id"], ob["id"], cid),
        paying(owner["id"], prop["id"], ob["id"], Decimal("3000.00")),
        paying(owner["id"], prop["id"], ob["id"], Decimal("3000.00")),
    ])
    assert sorted(str(r[0]) for r in results) == ["409", "409", "ok"]
    final = obligation(client, owner, prop, ob["id"])
    assert final["amount_paid"] == "3000.00" and final["outstanding"] == "2000.00"
    assert len(final["payments"]) == 1
    reconcile(db_session, ob["id"])


def test_concurrent_confirmation_of_one_claim_pays_once(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    cid = claim(client, tenant, ob["id"], "1000.00").json()["id"]
    results = race([confirmer(owner["id"], prop["id"], ob["id"], cid)] * 4)
    assert sorted(str(r[0]) for r in results) == ["409", "409", "409", "ok"]
    assert obligation(client, owner, prop, ob["id"])["amount_paid"] == "1000.00"
    assert db_session.execute(text("select count(*) from rent_payments")).scalar() == 1
    assert db_session.execute(text(
        "select count(*) from notifications where kind='payment_claim_confirmed'")).scalar() == 1


# ------------------------------------------------------------- notifications


def test_notifications_go_to_the_right_people_once(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world)
    other_owner, other_tenant = world.owner("x"), world.tenant("o")
    owner["owner"] = True
    other_owner["owner"] = True

    cid = claim(client, tenant, ob["id"], "1000.00").json()["id"]
    (n,) = notifs(client, owner)
    assert n["kind"] == "payment_claim_submitted" and n["claim_id"] == cid
    assert "Tina Tenant" in n["body"] and n["read_at"] is None
    assert notifs(client, other_owner) == [] and notifs(client, other_tenant) == []
    assert notifs(client, tenant) == []  # the claimant is not notified of their own submission

    decide(client, owner, prop, ob["id"], cid, "confirm")
    decide(client, owner, prop, ob["id"], cid, "confirm")  # refused repeat -> no extra notification
    (t,) = notifs(client, tenant)
    assert t["kind"] == "payment_claim_confirmed" and "4000.00 is still outstanding" in t["body"]
    assert notifs(client, other_tenant) == []

    c2 = claim(client, tenant, ob["id"], "500.00").json()["id"]
    decide(client, owner, prop, ob["id"], c2, "reject", {"reason": "not received"})
    kinds = sorted(x["kind"] for x in notifs(client, tenant))
    assert kinds == ["payment_claim_confirmed", "payment_claim_rejected"]

    # read marking is recipient-scoped
    assert client.post(f"/api/v1/notifications/{n['id']}/read", headers=other_owner["headers"]).status_code == 404
    assert client.post(f"/api/v1/notifications/{n['id']}/read", headers=owner["headers"]).json()["read_at"]
    assert client.post(f"/api/v1/notifications/{n['id']}/read", headers=tenant["headers"]).status_code == 401
    unread = client.get("/api/v1/notifications?unread_only=true", headers=owner["headers"]).json()
    assert unread["total"] == 1  # the second claim's notification
    assert client.get("/api/v1/notifications").status_code == 401


# ------------------------------------------------------------------- reminders


def count_kind(db, kind):
    return db.execute(text("select count(*) from notifications where kind=:k"), {"k": kind}).scalar()


def test_pending_claims_do_not_suppress_reminders_and_sweep_is_idempotent(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world, due=5)
    claim(client, tenant, ob["id"], "5000.00")  # pending, unverified
    due = day(5)

    sent = reminder_service.run_sweep(db_session, today=due - timedelta(days=6))
    assert sent == {"owner_due_soon": 1, "tenant_due_soon": 0, "overdue": 0}
    sent = reminder_service.run_sweep(db_session, today=due - timedelta(days=2))
    assert sent == {"owner_due_soon": 0, "tenant_due_soon": 1, "overdue": 0}
    assert reminder_service.run_sweep(db_session, today=due - timedelta(days=1)) == {
        "owner_due_soon": 0, "tenant_due_soon": 0, "overdue": 0}  # no duplicates
    sent = reminder_service.run_sweep(db_session, today=due + timedelta(days=1))
    assert sent["overdue"] == 2  # owner + tenant, despite the pending claim
    assert reminder_service.run_sweep(db_session, today=due + timedelta(days=2))["overdue"] == 0
    assert count_kind(db_session, "rent_overdue") == 2


def test_settled_obligation_gets_no_reminders_after_confirmation(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world, due=1)
    cid = claim(client, tenant, ob["id"], "5000.00").json()["id"]
    decide(client, owner, prop, ob["id"], cid, "confirm")
    for offset in (-6, -2, 0, 3):
        sent = reminder_service.run_sweep(db_session, today=day(1) + timedelta(days=offset))
        assert sent == {"owner_due_soon": 0, "tenant_due_soon": 0, "overdue": 0}
    assert count_kind(db_session, "rent_due_soon_owner") == 0
    assert count_kind(db_session, "rent_overdue") == 0


def test_partial_payment_still_reminds_and_reversal_reopens(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world, due=1)
    cid = claim(client, tenant, ob["id"], "1000.00").json()["id"]
    decide(client, owner, prop, ob["id"], cid, "confirm")
    sent = reminder_service.run_sweep(db_session, today=day(1))
    assert sent["tenant_due_soon"] == 1
    body = db_session.execute(text(
        "select body from notifications where kind='rent_due_soon_tenant'")).scalar()
    assert "4000.00" in body  # reminder reflects the verified balance only


def test_overdue_status_uses_verified_payments_only(client, world, db_session):
    owner, prop, tenant, stay, ob = setup(world, due=1)
    claim(client, tenant, ob["id"], "5000.00")  # pending
    db_session.execute(text("update rent_obligations set due_date = current_date - 3"))
    db_session.commit()
    after = obligation(client, owner, prop, ob["id"])
    assert after["payment_status"] == "overdue" and after["outstanding"] == "5000.00"


def test_generate_monthly_obligations_is_idempotent(client, world, db_session):
    owner = world.owner()
    prop = world.property(owner)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, prop["beds"][0]["id"], days(0))
    assert rent_service.generate_monthly_obligations(db_session) == 0  # not checked in yet
    stay_action(client, owner, prop, stay["id"], "check-in")
    first = rent_service.generate_monthly_obligations(db_session)
    assert first == 1
    assert rent_service.generate_monthly_obligations(db_session) == 0
    row = db_session.execute(text("select amount_due, due_date from rent_obligations")).one()
    assert row[0] == Decimal("5000.00") and row[1] == business_today()


def test_obligation_creation_rules(client, world):
    owner, prop, tenant, stay, ob = setup(world)
    url = f"{pp(prop)}/stays/{stay['id']}/rent-obligations"
    body = {"period_start": days(0), "due_date": days(5)}
    assert client.post(url, json=body, headers=owner["headers"]).status_code == 409  # duplicate period
    assert client.post(url, json={**body, "period_start": days(40), "amount": "0"},
                       headers=owner["headers"]).status_code == 422
    assert client.post(url, json=body, headers=tenant["headers"]).status_code == 401
    assert client.post(url, json=body, headers=world.owner("x")["headers"]).status_code == 404
    page = client.get(f"{pp(prop)}/rent-obligations?only_open=true", headers=owner["headers"]).json()
    assert page["total"] == 1 and page["items"][0]["tenant_full_name"] == "Tina Tenant"
    mine = client.get(f"{TP}/rent-obligations", headers=tenant["headers"]).json()
    assert mine["total"] == 1


def test_stranger_owner_cannot_record_or_reverse_payments(client, world):
    owner, prop, tenant, stay, ob = setup(world)
    stranger = world.owner("x")
    url = f"{pp(prop)}/rent-obligations/{ob['id']}/payments"
    body = {"amount": "100.00", "method": "cash", "paid_on": days(0)}
    assert client.post(url, json=body, headers=stranger["headers"]).status_code == 404
    assert client.post(url, json=body).status_code == 401
    assert obligation(client, owner, prop, ob["id"])["amount_paid"] == "0.00"


def test_recurring_job_entry_point_runs_end_to_end(client, world, db_session):
    """Runs `python -m app.jobs.rent_jobs` as a real process against the test DB."""
    import os
    import subprocess
    import sys

    from tests.conftest import BACKEND_DIR

    owner, prop, tenant, stay, ob = setup(world, due=3)
    result = subprocess.run(
        [sys.executable, "-m", "app.jobs.rent_jobs"],
        cwd=BACKEND_DIR, env=dict(os.environ), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr[-1500:]
    assert "'owner_due_soon': 1" in result.stdout
    assert count_kind(db_session, "rent_due_soon_owner") == 1
    again = subprocess.run(
        [sys.executable, "-m", "app.jobs.rent_jobs"],
        cwd=BACKEND_DIR, env=dict(os.environ), capture_output=True, text=True,
    )
    assert again.returncode == 0 and "'owner_due_soon': 0" in again.stdout  # idempotent
    assert count_kind(db_session, "rent_due_soon_owner") == 1
