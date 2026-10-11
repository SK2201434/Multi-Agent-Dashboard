"""Edge cases found by the Module 6 audit."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.property_tenant import PropertyTenant
from app.models.stay import Stay
from app.models.stay_bed_assignment import StayBedAssignment
from app.services import booking_service
from app.services.stay_rules import business_today, utc_now
from tests.conftest import days
from tests.flows import (
    TP,
    approve,
    availability,
    book,
    get_stay,
    pp,
    reserve,
    stay_action,
)

PUBLIC = "/api/v1/public/properties"


def make(world, beds=3):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=beds)
    return owner, prop, prop["beds"]


def make_late(db, stay_id, planned_in=-20, planned_out=-1):
    """Shift a stay and its assignment into the past (no time travel)."""
    db.execute(
        text("update stays set planned_check_in=current_date + :i, "
             "planned_check_out=current_date + :o where id=:s"),
        {"i": planned_in, "o": planned_out, "s": stay_id})
    db.execute(
        text("update stay_bed_assignments set period_start=current_date + :i, "
             "period_end=current_date + :o where stay_id=:s and released_at is null"),
        {"i": planned_in, "o": planned_out, "s": stay_id})
    db.commit()


# ------------------------------------------------ public property listing


def test_public_listing_is_anonymous_and_filters_and_hides_private_fields(client, world, db_session):
    owner = world.owner()
    visible = world.property(owner)
    hidden = world.property(owner)
    deleted = world.property(owner)
    db_session.execute(text("update properties set is_active=false where id=:i"), {"i": hidden["id"]})
    db_session.execute(text("update properties set deleted_at=now(), is_active=false where id=:i"),
                       {"i": deleted["id"]})
    db_session.execute(text("update properties set contact_phone='+911234567890', contact_email='o@x.com'"))
    db_session.commit()

    res = client.get(PUBLIC)  # no Authorization header
    assert res.status_code == 200
    ids = [p["id"] for p in res.json()]
    assert ids == [visible["id"]]
    leaked = {"owner_id", "contact_phone", "contact_email", "is_active", "deleted_at", "rooms"}
    assert not leaked & set(res.json()[0])
    assert "+911234567890" not in res.text and "o@x.com" not in res.text
    assert client.get(f"{PUBLIC}/{hidden['id']}").status_code == 404
    assert client.get(f"{PUBLIC}/{deleted['id']}").status_code == 404


# ------------------------------------- open-ended / late occupancy rules


def test_late_stay_blocks_same_tenant_on_other_bed_at_approval(client, world, db_session):
    """The tenant-level exclusion constraint only sees planned dates; the
    service must treat a checked-in stay past its checkout as still here."""
    owner, prop, beds = make(world)
    tenant = world.tenant()
    a = reserve(client, owner, prop, tenant, beds[0]["id"], days(0), days(3))
    stay_action(client, owner, prop, a["id"], "check-in")
    # valid today: starts after A's planned checkout
    rid = book(client, tenant, beds[1]["id"], days(5), days(10)).json()["id"]

    make_late(db_session, a["id"])  # A overstays its planned checkout
    assert approve(client, owner, prop, rid).status_code == 409
    assert book(client, tenant, beds[2]["id"], days(5), days(10)).status_code == 409

    stay_action(client, owner, prop, a["id"], "check-out")
    assert approve(client, owner, prop, rid).status_code == 200


def test_database_alone_would_allow_it_so_the_service_is_the_guard(client, world, db_session):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    a = reserve(client, owner, prop, tenant, beds[0]["id"], days(0), days(3))
    stay_action(client, owner, prop, a["id"], "check-in")
    make_late(db_session, a["id"])
    second = Stay(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant["id"]),
                  property_id=uuid.UUID(prop["id"]), status="reserved",
                  planned_check_in=business_today() + timedelta(days=5),
                  monthly_rent=Decimal("1.00"))
    db_session.add(second)
    db_session.commit()  # planned dates do not overlap -> the exclusion constraint passes
    assert db_session.get(Stay, second.id) is not None


def test_tenant_cannot_be_checked_in_to_two_stays(client, world, db_session):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    a = reserve(client, owner, prop, tenant, beds[0]["id"], days(0), days(3))
    b = reserve(client, owner, prop, tenant, beds[1]["id"], days(3), days(10))
    stay_action(client, owner, prop, a["id"], "check-in")
    make_late(db_session, a["id"], -20, -1)
    make_late(db_session, b["id"], -1, 7)

    res = stay_action(client, owner, prop, b["id"], "check-in")
    assert res.status_code == 409
    assert get_stay(client, owner, prop, b["id"])["status"] == "reserved"
    stay_action(client, owner, prop, a["id"], "check-out")
    assert stay_action(client, owner, prop, b["id"], "check-in").status_code == 200


def test_database_refuses_two_checked_in_stays_for_one_tenant(client, world, db_session):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    a = reserve(client, owner, prop, tenant, beds[0]["id"], days(0), days(3))
    stay_action(client, owner, prop, a["id"], "check-in")
    now = utc_now()
    dup = Stay(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant["id"]),
               property_id=uuid.UUID(prop["id"]), status="checked_in",
               actual_check_in_at=now,
               planned_check_in=business_today() + timedelta(days=50),
               monthly_rent=Decimal("1.00"))
    db_session.add(dup)
    with pytest.raises(IntegrityError) as err:
        db_session.commit()
    db_session.rollback()
    assert "uq_stays_tenant_checked_in" in str(err.value)


def test_cancel_and_no_show_do_not_fake_occupancy(client, world):
    owner, prop, beds = make(world)
    s1 = reserve(client, owner, prop, world.tenant(), beds[0]["id"], days(0))
    s2 = reserve(client, owner, prop, world.tenant("b"), beds[1]["id"], days(0))
    for stay, action in ((s1, "cancel"), (s2, "no-show")):
        body = stay_action(client, owner, prop, stay["id"], action).json()
        row = body["assignments"][0]
        assert row["occupied_from"] is None and row["released_at"] is not None
        assert body["actual_check_in_at"] is None and body["actual_check_out_at"] is None


def test_adjacent_reservations_for_bed_and_tenant_remain_valid(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    reserve(client, owner, prop, tenant, beds[0]["id"], days(10), days(20))
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(20), days(30))
    assert stay["status"] == "reserved"


def test_bed_change_cannot_land_on_a_late_occupied_bed(client, world, db_session):
    from tests.flows import ask_move

    owner, prop, beds = make(world)
    t1, t2 = world.tenant(), world.tenant("b")
    a = reserve(client, owner, prop, t1, beds[0]["id"], days(0), days(3))
    stay_action(client, owner, prop, a["id"], "check-in")
    b = reserve(client, owner, prop, t2, beds[1]["id"], days(5), days(15))
    # bed 0 looks free for [5, 15) today, so the request is accepted...
    rid = ask_move(client, t2, b["id"], beds[0]["id"]).json()["id"]
    make_late(db_session, a["id"])  # ...then t1 overstays on bed 0
    res = client.post(f"{pp(prop)}/bed-change-requests/{rid}/approve", headers=owner["headers"])
    assert res.status_code == 409
    assert get_stay(client, owner, prop, b["id"])["bed_id"] == beds[1]["id"]


# ------------------------------------------------------ property deletion


def test_property_delete_keeps_history_and_expires_pending_requests(client, world, db_session):
    owner, prop, beds = make(world)
    done = reserve(client, owner, prop, world.tenant(), beds[0]["id"], days(0))
    stay_action(client, owner, prop, done["id"], "check-in")
    stay_action(client, owner, prop, done["id"], "check-out")
    gone = reserve(client, owner, prop, world.tenant("b"), beds[1]["id"], days(10))
    stay_action(client, owner, prop, gone["id"], "cancel")
    waiting = world.tenant("w")
    rid = book(client, waiting, beds[2]["id"], days(10)).json()["id"]

    live = reserve(client, owner, prop, world.tenant("l"), beds[1]["id"], days(10))
    assert client.delete(pp(prop), headers=owner["headers"]).status_code == 409
    stay_action(client, owner, prop, live["id"], "cancel")
    assert client.delete(pp(prop), headers=owner["headers"]).status_code == 200

    count = lambda t: db_session.execute(text(f"select count(*) from {t}")).scalar()
    assert count("stays") == 3 and count("stay_bed_assignments") == 3
    assert count("booking_requests") >= 4
    got = client.get(f"{TP}/booking-requests/{rid}", headers=waiting["headers"]).json()
    assert got["status"] == "expired"
    assert client.get(f"{PUBLIC}/{prop['id']}").status_code == 404


# ----------------------------------------------------- association on approval


def test_failed_approval_leaves_no_association_stay_or_approved_status(client, world, db_session, monkeypatch):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    rid = book(client, tenant, beds[0]["id"], days(10)).json()["id"]

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure after the association and stay were flushed")

    monkeypatch.setattr(booking_service, "_expire_superseded", boom)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post(f"{pp(prop)}/booking-requests/{rid}/approve", headers=owner["headers"])
    assert res.status_code == 500
    monkeypatch.undo()

    count = lambda t: db_session.execute(text(f"select count(*) from {t}")).scalar()
    assert count("stays") == 0 and count("stay_bed_assignments") == 0
    assert count("property_tenants") == 0
    status = db_session.execute(text("select status from booking_requests")).scalar()
    assert status == "pending"
    assert approve(client, owner, prop, rid).status_code == 200  # still approvable


def test_conflicting_approval_creates_no_association(client, world, db_session):
    owner, prop, beds = make(world)
    t1, t2 = world.tenant(), world.tenant("b")
    r1 = book(client, t1, beds[0]["id"], days(10)).json()["id"]
    r2 = book(client, t2, beds[0]["id"], days(10)).json()["id"]
    assert approve(client, owner, prop, r1).status_code == 200
    assert approve(client, owner, prop, r2).status_code == 409
    rows = db_session.execute(text("select tenant_id::text from property_tenants")).scalars().all()
    assert rows == [t1["id"]]


def test_existing_association_is_reused_not_duplicated(client, world, db_session):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    db_session.add(PropertyTenant(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant["id"]),
                                  property_id=uuid.UUID(prop["id"]),
                                  created_by_owner_id=uuid.UUID(owner["id"])))
    db_session.commit()
    reserve(client, owner, prop, tenant, beds[0]["id"], days(10))
    n = db_session.execute(text("select count(*) from property_tenants where status='active'")).scalar()
    assert n == 1
