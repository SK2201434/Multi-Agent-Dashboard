import json

from sqlalchemy import text

from tests.conftest import days
from tests.flows import (
    ask_move,
    availability,
    bed_available,
    book,
    reserve,
    stay_action,
)

PUBLIC = "/api/v1/public/properties"


def make(world, beds_per_room=3, rooms=1):
    owner = world.owner()
    prop = world.property(owner, rooms=rooms, beds_per_room=beds_per_room)
    return owner, prop, prop["beds"]


def totals(client, prop, ci, co=None):
    body = availability(client, prop, ci, co).json()
    return body["total_bookable_beds"], body["total_available_beds"]


def test_public_endpoints_need_no_auth_and_list_rooms(client, world):
    owner, prop, beds = make(world, rooms=2)
    res = client.get(f"{PUBLIC}/{prop['id']}/rooms")
    assert res.status_code == 200
    rooms = res.json()
    assert len(rooms) == 2
    assert rooms[0]["monthly_rent_per_bed"] == "5000.00"
    assert rooms[0]["total_bookable_beds"] == 3
    assert client.get(PUBLIC).status_code == 200
    assert totals(client, prop, days(5)) == (6, 6)


def test_overlap_excluded_and_half_open_boundary(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    bed = beds[0]["id"]
    reserve(client, owner, prop, tenant, bed, days(10), days(20))
    assert not bed_available(client, prop, bed, days(10), days(20))
    assert not bed_available(client, prop, bed, days(15), days(16))
    assert not bed_available(client, prop, bed, days(5), days(11))
    assert not bed_available(client, prop, bed, days(19))  # open-ended query
    # [10, 20) then [20, 30): the checkout date is exclusive
    assert bed_available(client, prop, bed, days(20), days(30))
    assert bed_available(client, prop, bed, days(20))
    assert bed_available(client, prop, bed, days(1), days(10))
    assert totals(client, prop, days(12), days(14)) == (3, 2)
    assert totals(client, prop, days(25), days(26)) == (3, 3)


def test_null_checkout_blocks_everything_after_checkin(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    bed = beds[0]["id"]
    reserve(client, owner, prop, tenant, bed, days(10))  # open-ended
    assert not bed_available(client, prop, bed, days(10), days(11))
    assert not bed_available(client, prop, bed, days(400), days(401))
    assert not bed_available(client, prop, bed, days(400))
    assert bed_available(client, prop, bed, days(1), days(10))


def test_checked_in_stay_past_planned_checkout_still_blocks(client, world, db_session):
    owner, prop, beds = make(world)
    tenant, other = world.tenant(), world.tenant("other")
    bed = beds[0]["id"]
    stay = reserve(client, owner, prop, tenant, bed, days(0), days(3))
    assert stay_action(client, owner, prop, stay["id"], "check-in").status_code == 200
    assert bed_available(client, prop, bed, days(3), days(10))  # not yet late

    # Pretend the stay began long ago and its planned checkout has passed.
    db_session.execute(
        text("update stays set planned_check_in = current_date - 20, "
             "planned_check_out = current_date - 1"))
    db_session.execute(
        text("update stay_bed_assignments set period_start = current_date - 20, "
             "period_end = current_date - 1"))
    db_session.commit()

    assert not bed_available(client, prop, bed, days(5), days(10))
    assert not bed_available(client, prop, bed, days(5))
    assert book(client, other, bed, days(5), days(10)).status_code == 409

    # Actual checkout releases it.
    assert stay_action(client, owner, prop, stay["id"], "check-out").status_code == 200
    assert bed_available(client, prop, bed, days(5), days(10))


def test_released_states_do_not_block(client, world):
    owner, prop, beds = make(world)
    t1, t2, t3 = world.tenant(), world.tenant("b"), world.tenant("c")
    cancelled = reserve(client, owner, prop, t1, beds[0]["id"], days(10), days(20))
    no_show = reserve(client, owner, prop, t2, beds[1]["id"], days(0), days(20))
    done = reserve(client, owner, prop, t3, beds[2]["id"], days(0), days(20))
    assert totals(client, prop, days(12), days(14)) == (3, 0)

    assert stay_action(client, owner, prop, cancelled["id"], "cancel").status_code == 200
    assert stay_action(client, owner, prop, no_show["id"], "no-show").status_code == 200
    stay_action(client, owner, prop, done["id"], "check-in")
    assert totals(client, prop, days(12), days(14)) == (3, 2)
    stay_action(client, owner, prop, done["id"], "check-out")
    assert totals(client, prop, days(12), days(14)) == (3, 3)


def test_inactive_beds_rooms_properties_not_bookable(client, world, db_session):
    owner, prop, beds = make(world, rooms=2)
    db_session.execute(
        text("update beds set status='maintenance' where id = :i"), {"i": beds[0]["id"]})
    db_session.commit()
    assert totals(client, prop, days(5)) == (5, 5)
    db_session.execute(
        text("update rooms set is_active=false where id = :i"),
        {"i": prop["rooms"][1]["id"]})
    db_session.commit()
    assert totals(client, prop, days(5)) == (2, 2)
    db_session.execute(text("update properties set is_active=false"))
    db_session.commit()
    assert availability(client, prop, days(5)).status_code == 404
    assert client.get(f"{PUBLIC}/{prop['id']}/rooms").status_code == 404


def test_pending_requests_do_not_consume_availability(client, world):
    owner, prop, beds = make(world)
    tenant, other = world.tenant(), world.tenant("o")
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10), days(20))
    assert book(client, other, beds[1]["id"], days(10), days(20)).status_code == 201
    assert ask_move(client, tenant, stay["id"], beds[2]["id"]).status_code == 201
    assert bed_available(client, prop, beds[1]["id"], days(10), days(20))
    assert bed_available(client, prop, beds[2]["id"], days(10), days(20))
    assert not bed_available(client, prop, beds[0]["id"], days(10), days(20))


def test_invalid_query_dates(client, world):
    owner, prop, beds = make(world)
    assert availability(client, prop, days(5), days(5)).status_code == 422
    assert availability(client, prop, days(5), days(1)).status_code == 422
    assert client.get(f"{PUBLIC}/{prop['id']}/availability").status_code == 422


def test_public_responses_leak_nothing_private(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(0), days(20))
    stay_action(client, owner, prop, stay["id"], "check-in")
    book(client, world.tenant("x"), beds[1]["id"], days(5))

    bodies = [
        client.get(f"{PUBLIC}/{prop['id']}/rooms").text,
        availability(client, prop, days(1), days(5)).text,
    ]
    forbidden = [
        tenant["id"], tenant["email"], "Tina", "tenant", "password", "token",
        "owner_id", "phone", "email", "emergency", "reviewed", "rejection",
        "stay_id", "tenant_id", stay["id"],
    ]
    for body in bodies:
        lowered = body.lower()
        for word in forbidden:
            assert word.lower() not in lowered, word
    allowed = {"property_id", "check_in", "check_out", "total_bookable_beds",
               "total_available_beds", "rooms"}
    assert set(json.loads(bodies[1])) == allowed
