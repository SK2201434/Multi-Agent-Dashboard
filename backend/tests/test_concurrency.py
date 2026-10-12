"""Real PostgreSQL concurrency: competing transactions on separate sessions.

Each contender gets its own Session/connection and they are released
together by a barrier. Whatever the interleaving, exactly one may win and
the rest must get a clean AppError (409), with no partial state left.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.owner import Owner
from app.services import bed_change_service, booking_service
from app.services.errors import AppError
from tests.conftest import days
from tests.flows import ask_move, book, reserve

ROUNDS = 3


def race(fns):
    """Run callables(session) simultaneously; return [('ok'|status, value)]."""
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
        futures = [pool.submit(run, fn) for fn in fns]
        return [f.result(timeout=60) for f in futures]


def approver(owner_id, property_id, request_id):
    def call(session):
        owner = session.get(Owner, uuid.UUID(owner_id))
        return booking_service.approve(
            session, owner, uuid.UUID(property_id), uuid.UUID(request_id)
        ).id

    return call


def count(db, sql):
    return db.execute(text(sql)).scalar()


def outcomes(results):
    return sorted(str(r[0]) for r in results)


@pytest.mark.parametrize("round_", range(ROUNDS))
def test_competing_approvals_for_the_same_bed(client, world, db_session, round_):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=2)
    bed = prop["beds"][0]["id"]
    tenants = [world.tenant(f"t{i}") for i in range(4)]
    requests = [book(client, t, bed, days(10), days(30)).json()["id"] for t in tenants]

    results = race([approver(owner["id"], prop["id"], r) for r in requests])

    assert outcomes(results) == ["409", "409", "409", "ok"]
    assert count(db_session, "select count(*) from stays") == 1
    assert count(db_session, "select count(*) from stay_bed_assignments") == 1
    assert count(db_session, "select count(*) from booking_requests where status='approved'") == 1


def test_double_approval_of_the_same_request_creates_one_stay(client, world, db_session):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=1)
    rid = book(client, world.tenant(), prop["beds"][0]["id"], days(10)).json()["id"]
    results = race([approver(owner["id"], prop["id"], rid)] * 3)
    assert outcomes(results) == ["409", "409", "ok"]
    assert count(db_session, "select count(*) from stays") == 1


@pytest.mark.parametrize("round_", range(ROUNDS))
def test_one_tenant_cannot_win_two_overlapping_beds(client, world, db_session, round_):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=3)
    tenant = world.tenant()
    requests = [
        book(client, tenant, prop["beds"][i]["id"], days(10), days(30)).json()["id"]
        for i in range(3)
    ]
    results = race([approver(owner["id"], prop["id"], r) for r in requests])
    assert outcomes(results) == ["409", "409", "ok"]
    assert count(db_session, "select count(*) from stays") == 1
    assert count(
        db_session, "select count(*) from booking_requests where status='expired'"
    ) == 2  # the losers' siblings were superseded, never double-booked


@pytest.mark.parametrize("round_", range(6))
def test_same_tenant_many_simultaneous_approvals_leave_consistent_state(
    client, world, db_session, round_
):
    """Many approvals for one tenant: exactly one wins, the rest get 409,
    nothing is partially expired or double-assigned (no deadlock/500)."""
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=5)
    tenant = world.tenant()
    requests = [
        book(client, tenant, prop["beds"][i]["id"], days(10), days(30)).json()["id"]
        for i in range(5)
    ]
    results = race([approver(owner["id"], prop["id"], r) for r in requests])
    assert outcomes(results) == ["409"] * 4 + ["ok"]
    assert count(db_session, "select count(*) from stays") == 1
    assert count(db_session, "select count(*) from stay_bed_assignments") == 1
    statuses = dict(db_session.execute(text(
        "select status, count(*) from booking_requests group by status")).all())
    assert statuses == {"approved": 1, "expired": 4}
    assert count(db_session, "select count(*) from booking_requests where status='pending'") == 0
    assert count(db_session, "select count(*) from property_tenants") == 1


@pytest.mark.parametrize("round_", range(4))
def test_approvals_racing_with_sibling_cancel_and_reject(client, world, db_session, round_):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=4)
    tenant = world.tenant()
    ids_ = [
        book(client, tenant, prop["beds"][i]["id"], days(10), days(30)).json()["id"]
        for i in range(4)
    ]

    def cancel(request_id):
        def call(session):
            from app.models.tenant import Tenant

            t = session.get(Tenant, uuid.UUID(tenant["id"]))
            return booking_service.cancel_by_tenant(session, t, uuid.UUID(request_id)).id
        return call

    def reject(request_id):
        def call(session):
            o = session.get(Owner, uuid.UUID(owner["id"]))
            return booking_service.reject(
                session, o, uuid.UUID(prop["id"]), uuid.UUID(request_id), None
            ).id
        return call

    results = race([
        approver(owner["id"], prop["id"], ids_[0]),
        approver(owner["id"], prop["id"], ids_[1]),
        cancel(ids_[2]),
        reject(ids_[3]),
    ])
    assert [r[0] for r in results[:2]].count("ok") <= 1
    assert all(r[0] in ("ok", 409) for r in results)
    assert count(db_session, "select count(*) from stays") <= 1
    assert count(db_session, "select count(*) from stay_bed_assignments") == count(
        db_session, "select count(*) from stays")
    # a request is never left both approved and without a stay, or pending after a win
    if count(db_session, "select count(*) from stays") == 1:
        assert count(
            db_session, "select count(*) from booking_requests where status='approved'") == 1
        assert count(
            db_session, "select count(*) from booking_requests where status='pending'") == 0


def test_approving_missing_or_foreign_request_is_404(client, world):
    owner, other = world.owner(), world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=1)
    rid = book(client, world.tenant(), prop["beds"][0]["id"], days(10)).json()["id"]
    other_prop = world.property(other, rooms=1, beds_per_room=1)
    for pid, request_id, who in ((prop["id"], str(uuid.uuid4()), owner),
                                 (other_prop["id"], rid, other)):
        res = client.post(f"/api/v1/properties/{pid}/booking-requests/{request_id}/approve",
                          headers=who["headers"])
        assert res.status_code == 404, res.text


def test_competing_bed_changes_to_one_destination(client, world, db_session):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=3)
    beds = [b["id"] for b in prop["beds"]]
    t1, t2 = world.tenant("a"), world.tenant("b")
    s1 = reserve(client, owner, prop, t1, beds[0], days(10), days(30))
    s2 = reserve(client, owner, prop, t2, beds[1], days(10), days(30))
    r1 = ask_move(client, t1, s1["id"], beds[2]).json()["id"]
    r2 = ask_move(client, t2, s2["id"], beds[2]).json()["id"]

    def mover(request_id):
        def call(session):
            o = session.get(Owner, uuid.UUID(owner["id"]))
            return bed_change_service.approve(
                session, o, uuid.UUID(prop["id"]), uuid.UUID(request_id)
            ).id
        return call

    results = race([mover(r1), mover(r2)])
    assert outcomes(results) == ["409", "ok"]
    assert count(
        db_session,
        f"select count(*) from stay_bed_assignments where bed_id='{beds[2]}' and released_at is null",
    ) == 1
    # every live stay still has exactly one current assignment
    assert count(
        db_session,
        "select count(*) from (select stay_id from stay_bed_assignments "
        "where released_at is null group by stay_id having count(*) <> 1) x",
    ) == 0
    assert count(db_session, "select count(*) from bed_change_requests where status='approved'") == 1
    assert count(db_session, "select count(*) from bed_change_requests where status='pending'") == 1


def test_double_approval_of_one_bed_change(client, world, db_session):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=2)
    beds = [b["id"] for b in prop["beds"]]
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0], days(10))
    rid = ask_move(client, tenant, stay["id"], beds[1]).json()["id"]

    def mover(session):
        o = session.get(Owner, uuid.UUID(owner["id"]))
        return bed_change_service.approve(
            session, o, uuid.UUID(prop["id"]), uuid.UUID(rid)
        ).id

    assert outcomes(race([mover, mover, mover])) == ["409", "409", "ok"]
    assert count(db_session, "select count(*) from stay_bed_assignments") == 2
    assert count(
        db_session, "select count(*) from stay_bed_assignments where released_at is null"
    ) == 1


def test_database_blocks_double_booking_even_without_locks(client, world, db_session):
    """Two raw sessions inserting overlapping allocations: the second to
    commit must fail on the exclusion constraint (the last line of defence)."""
    from datetime import timedelta
    from decimal import Decimal

    from sqlalchemy.exc import IntegrityError

    from app.models.stay import Stay
    from app.models.stay_bed_assignment import StayBedAssignment
    from app.services.stay_rules import business_today, utc_now

    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=1)
    t1, t2 = world.tenant("a"), world.tenant("b")
    bed = uuid.UUID(prop["beds"][0]["id"])

    def make(session, tenant):
        today = business_today()
        stay = Stay(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant["id"]),
                    property_id=uuid.UUID(prop["id"]), status="reserved",
                    planned_check_in=today + timedelta(days=5),
                    monthly_rent=Decimal("1.00"))
        session.add(stay)
        session.flush()
        session.add(StayBedAssignment(
            id=uuid.uuid4(), stay_id=stay.id, bed_id=bed,
            period_start=stay.planned_check_in, assigned_at=utc_now()))
        session.flush()

    a, b = SessionLocal(), SessionLocal()
    try:
        make(a, t1)
        errors = []

        def second():
            try:
                make(b, t2)  # blocks on a's uncommitted row
                b.commit()
            except IntegrityError as exc:
                errors.append(str(exc))
                b.rollback()

        thread = threading.Thread(target=second)
        thread.start()
        a.commit()
        thread.join(timeout=20)
        assert errors and "ex_stay_bed_assignments_bed_no_overlap" in errors[0]
    finally:
        a.close()
        b.close()
    assert count(db_session, "select count(*) from stay_bed_assignments") == 1


def test_concurrent_check_ins_for_one_tenant_admit_only_one(client, world, db_session):
    from app.services import stay_service
    from tests.test_audit_edge_cases import make_late

    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=2)
    tenant = world.tenant()
    a = reserve(client, owner, prop, tenant, prop["beds"][0]["id"], days(1), days(3))
    b = reserve(client, owner, prop, tenant, prop["beds"][1]["id"], days(3), days(9))
    make_late(db_session, a["id"], -20, -10)
    make_late(db_session, b["id"], -10, 7)

    def checkin(stay_id):
        def call(session):
            o = session.get(Owner, uuid.UUID(owner["id"]))
            return stay_service.check_in(
                session, o, uuid.UUID(prop["id"]), uuid.UUID(stay_id)
            ).stay.id
        return call

    assert outcomes(race([checkin(a["id"]), checkin(b["id"])])) == ["409", "ok"]
    assert count(db_session, "select count(*) from stays where status='checked_in'") == 1
