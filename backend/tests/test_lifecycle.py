from sqlalchemy import text

from tests.conftest import days
from tests.flows import (
    TP,
    bed_available,
    book,
    get_stay,
    pp,
    reserve,
    stay_action,
)


def make(world):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=3)
    return owner, prop, prop["beds"]


def test_check_in_only_for_eligible_reservations(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    future = reserve(client, owner, prop, tenant, beds[0]["id"], days(10), days(20))
    res = stay_action(client, owner, prop, future["id"], "check-in")
    assert res.status_code == 409  # before planned check-in

    today = reserve(client, owner, prop, tenant, beds[0]["id"], days(0), days(5))
    res = stay_action(client, owner, prop, today["id"], "check-in")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "checked_in"
    assert body["actual_check_in_at"] is not None
    assert body["assignments"][0]["occupied_from"] == body["actual_check_in_at"]


def test_duplicate_and_out_of_order_actions_fail(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(0))
    sid = stay["id"]
    assert stay_action(client, owner, prop, sid, "check-out").status_code == 409
    first = stay_action(client, owner, prop, sid, "check-in")
    assert first.status_code == 200
    assert stay_action(client, owner, prop, sid, "check-in").status_code == 409
    # timestamp not silently overwritten
    assert get_stay(client, owner, prop, sid)["actual_check_in_at"] == first.json()["actual_check_in_at"]
    # a checked-in stay can be neither cancelled nor marked no-show
    assert stay_action(client, owner, prop, sid, "cancel").status_code == 409
    assert stay_action(client, owner, prop, sid, "no-show").status_code == 409

    out = stay_action(client, owner, prop, sid, "check-out")
    assert out.status_code == 200
    body = out.json()
    assert body["status"] == "checked_out"
    assert body["actual_check_out_at"] is not None
    assert body["assignments"][0]["released_at"] == body["actual_check_out_at"]
    first_out = body["actual_check_out_at"]
    for action in ("check-out", "check-in", "cancel", "no-show"):
        assert stay_action(client, owner, prop, sid, action).status_code == 409, action
    assert get_stay(client, owner, prop, sid)["actual_check_out_at"] == first_out


def test_cancel_and_no_show_rules_and_history_kept(client, world, db_session):
    owner, prop, beds = make(world)
    t1, t2, t3 = world.tenant(), world.tenant("b"), world.tenant("c")
    to_cancel = reserve(client, owner, prop, t1, beds[0]["id"], days(10), days(20))
    res = stay_action(client, owner, prop, to_cancel["id"], "cancel", {"reason": "changed mind"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "cancelled" and body["close_reason"] == "changed mind"
    assert body["closed_at"] is not None
    assert body["assignments"][0]["released_at"] is not None  # history kept
    assert db_session.execute(text("select count(*) from stays")).scalar() == 1
    for action in ("cancel", "no-show", "check-in", "check-out"):
        assert stay_action(client, owner, prop, to_cancel["id"], action).status_code == 409

    # no-show is deliberate, and not possible before the check-in date
    early = reserve(client, owner, prop, t2, beds[1]["id"], days(10))
    assert stay_action(client, owner, prop, early["id"], "no-show").status_code == 409
    due = reserve(client, owner, prop, t3, beds[2]["id"], days(0))
    res = stay_action(client, owner, prop, due["id"], "no-show")
    assert res.status_code == 200 and res.json()["status"] == "no_show"
    assert stay_action(client, owner, prop, due["id"], "check-in").status_code == 409


def test_reserved_stay_is_not_auto_no_show_when_date_passes(client, world, db_session):
    owner, prop, beds = make(world)
    stay = reserve(client, owner, prop, world.tenant(), beds[0]["id"], days(0), days(5))
    db_session.execute(
        text("update stays set planned_check_in = current_date - 30, "
             "planned_check_out = current_date - 25"))
    db_session.execute(
        text("update stay_bed_assignments set period_start = current_date - 30, "
             "period_end = current_date - 25"))
    db_session.commit()
    assert get_stay(client, owner, prop, stay["id"])["status"] == "reserved"
    # late check-in is still allowed
    assert stay_action(client, owner, prop, stay["id"], "check-in").status_code == 200


def test_rent_snapshot_survives_price_changes(client, world):
    owner, prop, beds = make(world)
    stay = reserve(client, owner, prop, world.tenant(), beds[0]["id"], days(0))
    room_id = prop["rooms"][0]["id"]
    res = client.patch(
        f"{pp(prop)}/rooms/{room_id}",
        json={"monthly_rent_per_bed": "9999.00"},
        headers=owner["headers"],
    )
    assert res.status_code == 200
    stay_action(client, owner, prop, stay["id"], "check-in")
    body = get_stay(client, owner, prop, stay["id"])
    assert body["monthly_rent"] == "5000.00"
    # a new reservation picks up the new price
    other = reserve(client, owner, prop, world.tenant("o"), beds[1]["id"], days(0))
    assert other["monthly_rent"] == "9999.00"


def test_checkout_releases_bed_for_a_following_stay(client, world):
    owner, prop, beds = make(world)
    t1, t2 = world.tenant(), world.tenant("b")
    bed = beds[0]["id"]
    first = reserve(client, owner, prop, t1, bed, days(0), days(10))
    stay_action(client, owner, prop, first["id"], "check-in")
    second = reserve(client, owner, prop, t2, bed, days(10), days(20))  # adjacent: allowed
    # bed still physically occupied: the next tenant cannot check in
    assert stay_action(client, owner, prop, second["id"], "check-in").status_code == 409
    stay_action(client, owner, prop, first["id"], "check-out")
    assert bed_available(client, prop, bed, days(0), days(10))


def test_check_in_requires_active_association_and_operational_bed(client, world, db_session):
    owner, prop, beds = make(world)
    stay = reserve(client, owner, prop, world.tenant(), beds[0]["id"], days(0))
    db_session.execute(text("update beds set status='maintenance'"))
    db_session.commit()
    assert stay_action(client, owner, prop, stay["id"], "check-in").status_code == 409
    db_session.execute(text("update beds set status='available'"))
    db_session.execute(text("update property_tenants set status='ended', ended_at=now()"))
    db_session.commit()
    assert stay_action(client, owner, prop, stay["id"], "check-in").status_code == 409


def test_owner_stay_listing_filters_and_pagination(client, world):
    owner, prop, beds = make(world)
    for i in range(3):
        reserve(client, owner, prop, world.tenant(f"t{i}"), beds[i]["id"], days(10))
    page = client.get(f"{pp(prop)}/stays?limit=2", headers=owner["headers"]).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["bed_label"] and page["items"][0]["tenant_full_name"]
    res = client.get(f"{pp(prop)}/stays?status=checked_in", headers=owner["headers"])
    assert res.json()["total"] == 0


def test_tenant_sees_only_own_stays(client, world):
    owner, prop, beds = make(world)
    t1, t2 = world.tenant(), world.tenant("b")
    stay = reserve(client, owner, prop, t1, beds[0]["id"], days(10))
    assert client.get(f"{TP}/stays/{stay['id']}", headers=t1["headers"]).status_code == 200
    assert client.get(f"{TP}/stays/{stay['id']}", headers=t2["headers"]).status_code == 404
    assert client.get(f"{TP}/stays", headers=t2["headers"]).json()["total"] == 0
    body = client.get(f"{TP}/stays/{stay['id']}", headers=t1["headers"]).json()
    assert "created_by_owner_id" not in body


def test_property_with_live_stays_cannot_be_deleted(client, world):
    owner, prop, beds = make(world)
    stay = reserve(client, owner, prop, world.tenant(), beds[0]["id"], days(10))
    assert client.delete(pp(prop), headers=owner["headers"]).status_code == 409
    stay_action(client, owner, prop, stay["id"], "cancel")
    assert client.delete(pp(prop), headers=owner["headers"]).status_code == 200
    assert book(client, world.tenant("n"), beds[1]["id"]).status_code == 404
