from sqlalchemy import text

from tests.conftest import days
from tests.flows import TP, approve, bed_available, book, pp, reject, reserve


def setup(world):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=3)
    return owner, prop, prop["beds"]


def test_tenant_can_create_request_with_optional_checkout(client, world):
    owner, prop, beds = setup(world)
    tenant = world.tenant()
    res = book(client, tenant, beds[0]["id"], days(10), message="hello")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["planned_check_out"] is None
    assert body["status"] == "pending"
    assert body["tenant_id"] == tenant["id"]
    assert body["quoted_monthly_rent"] == "5000.00"

    res = book(client, tenant, beds[1]["id"], days(10), days(20))
    assert res.status_code == 201
    assert res.json()["planned_check_out"] == days(20)


def test_tenant_id_rent_and_status_cannot_be_spoofed(client, world):
    owner, prop, beds = setup(world)
    tenant, other = world.tenant(), world.tenant("other")
    for extra in (
        {"tenant_id": other["id"]},
        {"monthly_rent": "1.00"},
        {"status": "approved"},
    ):
        res = book(client, tenant, beds[0]["id"], days(10), **extra)
        assert res.status_code == 422, extra
    # Approval snapshots the room's rent, never a client figure.
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10))
    assert stay["monthly_rent"] == "5000.00"
    assert stay["tenant_id"] == tenant["id"]


def test_invalid_dates_and_beds_rejected(client, world):
    owner, prop, beds = setup(world)
    tenant = world.tenant()
    assert book(client, tenant, beds[0]["id"], days(10), days(10)).status_code == 422
    assert book(client, tenant, beds[0]["id"], days(10), days(5)).status_code == 422
    assert book(client, tenant, beds[0]["id"], days(-1)).status_code == 422
    missing = "00000000-0000-4000-8000-000000000000"
    assert book(client, tenant, missing, days(10)).status_code == 404


def test_inactive_property_room_and_bed_not_bookable(client, world, db_session):
    owner, prop, beds = setup(world)
    tenant = world.tenant()
    res = client.patch(
        f"{pp(prop)}/rooms/{prop['rooms'][0]['id']}/beds/{beds[0]['id']}",
        json={"status": "maintenance"},
        headers=owner["headers"],
    )
    assert res.status_code == 200
    assert book(client, tenant, beds[0]["id"]).status_code == 409

    db_session.execute(text("update rooms set is_active=false"))
    db_session.commit()
    assert book(client, tenant, beds[1]["id"]).status_code == 404

    db_session.execute(text("update rooms set is_active=true"))
    db_session.execute(text("update properties set is_active=false"))
    db_session.commit()
    assert book(client, tenant, beds[1]["id"]).status_code == 404


def test_listing_get_and_cancel_are_own_only(client, world):
    owner, prop, beds = setup(world)
    t1, t2 = world.tenant(), world.tenant("two")
    rid = book(client, t1, beds[0]["id"]).json()["id"]
    assert client.get(f"{TP}/booking-requests/{rid}", headers=t1["headers"]).status_code == 200
    assert client.get(f"{TP}/booking-requests/{rid}", headers=t2["headers"]).status_code == 404
    assert client.post(f"{TP}/booking-requests/{rid}/cancel", headers=t2["headers"]).status_code == 404
    page = client.get(f"{TP}/booking-requests", headers=t2["headers"]).json()
    assert page["items"] == [] and page["total"] == 0
    page = client.get(f"{TP}/booking-requests?limit=1", headers=t1["headers"]).json()
    assert page["total"] == 1 and len(page["items"]) == 1

    res = client.post(f"{TP}/booking-requests/{rid}/cancel", headers=t1["headers"])
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    assert client.post(f"{TP}/booking-requests/{rid}/cancel", headers=t1["headers"]).status_code == 409
    assert approve(client, owner, prop, rid).status_code == 409


def test_pending_requests_do_not_block_availability(client, world):
    owner, prop, beds = setup(world)
    t1, t2 = world.tenant(), world.tenant("two")
    assert book(client, t1, beds[0]["id"], days(10)).status_code == 201
    assert bed_available(client, prop, beds[0]["id"], days(10))
    assert book(client, t2, beds[0]["id"], days(10)).status_code == 201


def test_duplicate_pending_request_is_rejected(client, world):
    owner, prop, beds = setup(world)
    t1 = world.tenant()
    assert book(client, t1, beds[0]["id"], days(10)).status_code == 201
    assert book(client, t1, beds[0]["id"], days(10), days(20)).status_code == 409


def test_approval_creates_exactly_one_reservation(client, world, db_session):
    owner, prop, beds = setup(world)
    tenant = world.tenant()
    rid = book(client, tenant, beds[0]["id"], days(10), days(40)).json()["id"]
    res = approve(client, owner, prop, rid)
    assert res.status_code == 200, res.text
    stay = res.json()
    assert stay["status"] == "reserved"
    assert stay["booking_request_id"] == rid
    assert stay["bed_id"] == beds[0]["id"]
    assert len(stay["assignments"]) == 1
    assert stay["assignments"][0]["occupied_from"] is None  # planned, not occupied

    assert approve(client, owner, prop, rid).status_code == 409
    assert db_session.execute(text("select count(*) from stays")).scalar() == 1
    assert db_session.execute(text("select count(*) from stay_bed_assignments")).scalar() == 1

    req = client.get(f"{pp(prop)}/booking-requests/{rid}", headers=owner["headers"]).json()
    assert req["status"] == "approved" and req["stay_id"] == stay["id"]
    assert db_session.execute(
        text("select count(*) from property_tenants where status='active'")
    ).scalar() == 1


def test_rejection_creates_no_reservation(client, world, db_session):
    owner, prop, beds = setup(world)
    tenant = world.tenant()
    rid = book(client, tenant, beds[0]["id"]).json()["id"]
    res = reject(client, owner, prop, rid, "Not available")
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    assert res.json()["rejection_reason"] == "Not available"
    assert db_session.execute(text("select count(*) from stays")).scalar() == 0
    assert reject(client, owner, prop, rid).status_code == 409
    assert approve(client, owner, prop, rid).status_code == 409


def test_approval_conflicts_when_bed_taken(client, world):
    owner, prop, beds = setup(world)
    t1, t2 = world.tenant(), world.tenant("two")
    r1 = book(client, t1, beds[0]["id"], days(10), days(20)).json()["id"]
    r2 = book(client, t2, beds[0]["id"], days(15)).json()["id"]
    assert approve(client, owner, prop, r1).status_code == 200
    assert approve(client, owner, prop, r2).status_code == 409
    req = client.get(f"{pp(prop)}/booking-requests/{r2}", headers=owner["headers"]).json()
    assert req["status"] == "pending" and req["stay_id"] is None


def test_tenant_cannot_hold_overlapping_stays_on_two_beds(client, world):
    owner, prop, beds = setup(world)
    tenant = world.tenant()
    r1 = book(client, tenant, beds[0]["id"], days(10), days(20)).json()["id"]
    r2 = book(client, tenant, beds[1]["id"], days(15), days(25)).json()["id"]
    assert approve(client, owner, prop, r1).status_code == 200
    assert approve(client, owner, prop, r2).status_code == 409
    assert book(client, tenant, beds[2]["id"], days(12)).status_code == 409
    # adjacent (half-open) dates are fine
    assert book(client, tenant, beds[2]["id"], days(20), days(30)).status_code == 201


def test_approval_expires_the_same_tenants_conflicting_pending_requests(client, world):
    owner, prop, beds = setup(world)
    tenant = world.tenant()
    r1 = book(client, tenant, beds[0]["id"], days(10), days(20)).json()["id"]
    r2 = book(client, tenant, beds[1]["id"], days(15), days(25)).json()["id"]
    r3 = book(client, tenant, beds[1]["id"], days(20), days(30)).json()["id"]
    assert approve(client, owner, prop, r1).status_code == 200

    def get(r):
        return client.get(f"{TP}/booking-requests/{r}", headers=tenant["headers"]).json()

    assert get(r2)["status"] == "expired"
    assert get(r3)["status"] == "pending"  # adjacent, still honourable


def test_owner_listing_is_paginated_and_scoped(client, world):
    owner, prop, beds = setup(world)
    other_owner = world.owner("other")
    tenant = world.tenant()
    for i in range(3):
        book(client, tenant, beds[i]["id"], days(10 + i * 30), days(20 + i * 30))
    page = client.get(f"{pp(prop)}/booking-requests?limit=2", headers=owner["headers"]).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["tenant_full_name"] == "Tina Tenant"
    assert client.get(f"{pp(prop)}/booking-requests", headers=other_owner["headers"]).status_code == 404
    assert client.get(f"{pp(prop)}/booking-requests?status=bogus", headers=owner["headers"]).status_code == 422


def test_request_must_belong_to_the_property_in_the_url(client, world):
    owner, prop, beds = setup(world)
    prop2 = world.property(owner)
    tenant = world.tenant()
    rid = book(client, tenant, beds[0]["id"]).json()["id"]
    assert approve(client, owner, prop2, rid).status_code == 404
