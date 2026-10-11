"""PostgreSQL-level guarantees, exercised directly against the schema.

These bypass the services on purpose: they prove the database itself
refuses states the application must never produce.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.stay import Stay
from app.models.stay_bed_assignment import StayBedAssignment
from app.services.stay_rules import utc_now
from tests.conftest import day


@pytest.fixture()
def ctx(client, world, db_session):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=3)
    tenants = [world.tenant(f"t{i}") for i in range(3)]
    return {
        "db": db_session,
        "property_id": uuid.UUID(prop["id"]),
        "beds": [uuid.UUID(b["id"]) for b in prop["beds"]],
        "tenants": [uuid.UUID(t["id"]) for t in tenants],
    }


def new_stay(ctx, tenant=0, ci=10, co=None, status="reserved", rent="4000.00"):
    now = utc_now()
    extra = {}
    if status == "checked_in":
        extra["actual_check_in_at"] = now
    elif status in ("cancelled", "no_show"):
        extra["closed_at"] = now
    elif status == "checked_out":
        extra.update(actual_check_in_at=now, actual_check_out_at=now)
    return Stay(
        id=uuid.uuid4(),
        tenant_id=ctx["tenants"][tenant],
        property_id=ctx["property_id"],
        status=status,
        planned_check_in=day(ci),
        planned_check_out=None if co is None else day(co),
        monthly_rent=Decimal(rent),
        **extra,
    )


def assign(ctx, stay, bed=0, ci=10, co=None, occupied=False, released=False):
    now = utc_now()
    return StayBedAssignment(
        id=uuid.uuid4(),
        stay_id=stay.id,
        bed_id=ctx["beds"][bed],
        period_start=day(ci),
        period_end=None if co is None else day(co),
        assigned_at=now,
        occupied_from=now if occupied else None,
        released_at=now + timedelta(seconds=1) if released else None,
    )


def commit_ok(ctx, *objs):
    db = ctx["db"]
    db.add_all(objs)
    db.commit()


def commit_fails(ctx, *objs, match=None):
    db = ctx["db"]
    db.add_all(objs)
    with pytest.raises(IntegrityError) as err:
        db.commit()
    db.rollback()
    if match:
        assert match in str(err.value)


def test_planned_checkout_is_optional_and_rent_is_decimal(ctx):
    stay = new_stay(ctx, co=None)
    commit_ok(ctx, stay)
    saved = ctx["db"].get(Stay, stay.id)
    assert saved.planned_check_out is None
    assert saved.monthly_rent == Decimal("4000.00")
    assert isinstance(saved.monthly_rent, Decimal)


def test_checkout_must_be_after_checkin_when_given(ctx):
    commit_fails(ctx, new_stay(ctx, ci=10, co=10), match="ck_stays_planned_dates")
    commit_fails(ctx, new_stay(ctx, ci=10, co=5), match="ck_stays_planned_dates")
    commit_ok(ctx, new_stay(ctx, ci=10, co=11))


def test_negative_rent_and_bad_status_rejected(ctx):
    commit_fails(ctx, new_stay(ctx, rent="-1"), match="ck_stays_monthly_rent")
    stay = new_stay(ctx)
    stay.status = "bogus"
    commit_fails(ctx, stay, match="ck_stays_")


@pytest.mark.parametrize(
    "status,kwargs",
    [
        ("checked_in", {"actual_check_in_at": None}),
        ("reserved", {"actual_check_in_at": utc_now()}),
        ("cancelled", {"closed_at": None}),
        ("checked_out", {"actual_check_out_at": None}),
    ],
)
def test_lifecycle_consistency_is_enforced(ctx, status, kwargs):
    stay = new_stay(ctx, status=status)
    for key, value in kwargs.items():
        setattr(stay, key, value)
    commit_fails(ctx, stay, match="ck_stays_lifecycle_consistency")


def test_unknown_foreign_keys_rejected(ctx):
    stay = new_stay(ctx)
    stay.tenant_id = uuid.uuid4()
    commit_fails(ctx, stay, match="foreign key")


def test_overlapping_allocations_of_one_bed_are_refused(ctx):
    s1, s2 = new_stay(ctx, 0, 10, 20), new_stay(ctx, 1, 15, 25)
    commit_ok(ctx, s1, s2, assign(ctx, s1, 0, 10, 20))
    commit_fails(ctx, assign(ctx, s2, 0, 15, 25), match="ex_stay_bed_assignments_bed_no_overlap")


def test_adjacent_allocations_allowed_half_open(ctx):
    s1, s2 = new_stay(ctx, 0, 10, 20), new_stay(ctx, 1, 20, 30)
    commit_ok(ctx, s1, s2, assign(ctx, s1, 0, 10, 20), assign(ctx, s2, 0, 20, 30))


def test_open_ended_allocation_blocks_all_later_dates(ctx):
    s1, s2 = new_stay(ctx, 0, 10, None), new_stay(ctx, 1, 500, 510)
    commit_ok(ctx, s1, s2, assign(ctx, s1, 0, 10, None))
    commit_fails(ctx, assign(ctx, s2, 0, 500, 510), match="ex_stay_bed_assignments_bed_no_overlap")


def test_released_assignments_do_not_block_and_history_is_unbounded(ctx):
    s1, s2 = new_stay(ctx, 0, 10, 20), new_stay(ctx, 1, 10, 20)
    commit_ok(ctx, s1, s2, assign(ctx, s1, 0, 10, 20, released=True), assign(ctx, s2, 0, 10, 20))


def test_one_current_assignment_per_stay(ctx):
    s1 = new_stay(ctx, 0, 10, 20)
    commit_ok(ctx, s1, assign(ctx, s1, 0, 10, 20))
    commit_fails(ctx, assign(ctx, s1, 1, 10, 20), match="uq_stay_bed_assignments_current_stay")


def test_one_physical_occupant_per_bed_even_with_disjoint_plans(ctx):
    s1, s2 = new_stay(ctx, 0, 1, 5, "checked_in"), new_stay(ctx, 1, 50, 60, "checked_in")
    commit_ok(ctx, s1, s2, assign(ctx, s1, 0, 1, 5, occupied=True))
    commit_fails(ctx, assign(ctx, s2, 0, 50, 60, occupied=True), match="uq_stay_bed_assignments_bed_occupied")


def test_stay_assignment_intervals_cannot_overlap_in_time(ctx):
    s1 = new_stay(ctx)
    now = utc_now()
    first = StayBedAssignment(id=uuid.uuid4(), stay_id=s1.id, bed_id=ctx["beds"][0],
                              period_start=day(10), period_end=None,
                              assigned_at=now, released_at=now + timedelta(hours=2))
    second = StayBedAssignment(id=uuid.uuid4(), stay_id=s1.id, bed_id=ctx["beds"][1],
                               period_start=day(10), period_end=None,
                               assigned_at=now + timedelta(hours=1))
    commit_fails(ctx, s1, first, second, match="ex_stay_bed_assignments_stay_timeline")
    # touching intervals tile correctly
    ctx["db"].rollback()
    third = StayBedAssignment(id=uuid.uuid4(), stay_id=s1.id, bed_id=ctx["beds"][1],
                              period_start=day(10), period_end=None,
                              assigned_at=now + timedelta(hours=2))
    commit_ok(ctx, s1, first, third)


def test_assignment_time_ordering_checks(ctx):
    s1 = new_stay(ctx)
    row = assign(ctx, s1, 0, 10, 20)
    row.released_at = row.assigned_at - timedelta(seconds=5)
    commit_fails(ctx, s1, row, match="released_after_assigned")
    bad_period = assign(ctx, new_stay(ctx, 1), 1, 10, 10)
    commit_fails(ctx, bad_period, match="ck_stay_bed_assignments_period")


def test_tenant_cannot_have_two_overlapping_live_stays(ctx):
    a, b = new_stay(ctx, 0, 10, 20), new_stay(ctx, 0, 15, 25)
    commit_ok(ctx, a)
    commit_fails(ctx, b, match="ex_stays_tenant_no_overlap")
    # adjacent is fine, terminal stays do not count, open-ended blocks later dates
    commit_ok(ctx, new_stay(ctx, 0, 20, 30))
    commit_ok(ctx, new_stay(ctx, 0, 12, 14, "cancelled"))
    commit_ok(ctx, new_stay(ctx, 1, 10, None))
    commit_fails(ctx, new_stay(ctx, 1, 900, 901), match="ex_stays_tenant_no_overlap")
