from sqlalchemy import text

from tests.conftest import days
from tests.flows import (
    TP,
    approve,
    ask_move,
    bed_available,
    book,
    get_stay,
    pp,
    reserve,
    stay_action,
)


def make(world, beds=4):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=beds)
    return owner, prop, prop["beds"]


def bc(prop, rid, action):
    return f"{pp(prop)}/bed-change-requests/{rid}/{action}"


def test_request_validation_and_pending_changes_nothing(client, world):
    owner, prop, beds = make(world)
    tenant, other = world.tenant(), world.tenant("b")
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10), days(20))

    res = ask_move(client, tenant, stay["id"], beds[1]["id"], "closer to window")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "pending"
    assert body["from_bed_id"] == beds[0]["id"] and body["to_bed_id"] == beds[1]["id"]

    # nothing moved, nothing reserved
    assert get_stay(client, owner, prop, stay["id"])["bed_id"] == beds[0]["id"]
    assert bed_available(client, prop, beds[1]["id"], days(10), days(20))

    assert ask_move(client, tenant, stay["id"], beds[2]["id"]).status_code == 409  # duplicate pending
    assert ask_move(client, other, stay["id"], beds[2]["id"]).status_code == 404  # not their stay
    assert ask_move(client, tenant, stay["id"], beds[0]["id"]).status_code == 422  # same bed


def test_cross_property_move_rejected(client, world):
    owner, prop, beds = make(world)
    prop2 = world.property(owner)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10))
    res = ask_move(client, tenant, stay["id"], prop2["beds"][0]["id"])
    assert res.status_code == 422


def test_destination_must_be_free_and_bookable(client, world, db_session):
    owner, prop, beds = make(world)
    t1, t2 = world.tenant(), world.tenant("b")
    stay = reserve(client, owner, prop, t1, beds[0]["id"], days(10), days(20))
    reserve(client, owner, prop, t2, beds[1]["id"], days(15), days(25))
    assert ask_move(client, t1, stay["id"], beds[1]["id"]).status_code == 409
    db_session.execute(
        text("update beds set status='maintenance' where id=:i"), {"i": beds[2]["id"]})
    db_session.commit()
    assert ask_move(client, t1, stay["id"], beds[2]["id"]).status_code == 409
    assert ask_move(client, t1, stay["id"], beds[3]["id"]).status_code == 201


def test_rejection_preserves_assignment(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10))
    rid = ask_move(client, tenant, stay["id"], beds[1]["id"]).json()["id"]
    res = client.post(bc(prop, rid, "reject"), json={"reason": "no"}, headers=owner["headers"])
    assert res.status_code == 200
    assert res.json()["status"] == "rejected" and res.json()["rejection_reason"] == "no"
    after = get_stay(client, owner, prop, stay["id"])
    assert after["bed_id"] == beds[0]["id"] and len(after["assignments"]) == 1
    assert client.post(bc(prop, rid, "approve"), headers=owner["headers"]).status_code == 409
    # tenant can ask again after a rejection
    assert ask_move(client, tenant, stay["id"], beds[1]["id"]).status_code == 201


def test_approval_for_future_reservation_keeps_history_and_rent(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10), days(20))
    rid = ask_move(client, tenant, stay["id"], beds[1]["id"]).json()["id"]
    res = client.post(bc(prop, rid, "approve"), headers=owner["headers"])
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved" and res.json()["effective_at"]

    after = get_stay(client, owner, prop, stay["id"])
    assert after["status"] == "reserved"
    assert after["bed_id"] == beds[1]["id"]
    assert after["monthly_rent"] == stay["monthly_rent"]
    old, new = after["assignments"]
    assert old["bed_id"] == beds[0]["id"] and new["bed_id"] == beds[1]["id"]
    assert old["released_at"] == new["assigned_at"] and new["released_at"] is None
    assert new["bed_change_request_id"] == rid
    assert new["occupied_from"] is None  # still only planned
    # old bed free again, new bed held
    assert bed_available(client, prop, beds[0]["id"], days(10), days(20))
    assert not bed_available(client, prop, beds[1]["id"], days(10), days(20))
    assert client.post(bc(prop, rid, "approve"), headers=owner["headers"]).status_code == 409


def test_approval_for_checked_in_stay_moves_occupancy(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(0))
    checked_in = stay_action(client, owner, prop, stay["id"], "check-in").json()
    rid = ask_move(client, tenant, stay["id"], beds[1]["id"]).json()["id"]
    res = client.post(bc(prop, rid, "approve"), headers=owner["headers"])
    assert res.status_code == 200, res.text

    after = get_stay(client, owner, prop, stay["id"])
    assert after["status"] == "checked_in"
    assert after["actual_check_in_at"] == checked_in["actual_check_in_at"]
    old, new = after["assignments"]
    assert old["released_at"] == new["assigned_at"] == new["occupied_from"]
    assert old["occupied_from"] is not None
    assert new["released_at"] is None and new["bed_id"] == beds[1]["id"]
    assert after["monthly_rent"] == stay["monthly_rent"]
    # old bed is free from now on; new bed is held (open-ended)
    assert bed_available(client, prop, beds[0]["id"], days(0))
    assert not bed_available(client, prop, beds[1]["id"], days(5))
    # checkout closes only the current assignment
    out = stay_action(client, owner, prop, stay["id"], "check-out").json()
    assert out["assignments"][1]["released_at"] == out["actual_check_out_at"]


def test_destination_taken_before_approval_causes_conflict(client, world):
    owner, prop, beds = make(world)
    t1, t2 = world.tenant(), world.tenant("b")
    stay = reserve(client, owner, prop, t1, beds[0]["id"], days(10), days(20))
    rid = ask_move(client, t1, stay["id"], beds[1]["id"]).json()["id"]
    reserve(client, owner, prop, t2, beds[1]["id"], days(12), days(18))  # takes it first

    res = client.post(bc(prop, rid, "approve"), headers=owner["headers"])
    assert res.status_code == 409
    req = client.get(f"{pp(prop)}/bed-change-requests/{rid}", headers=owner["headers"]).json()
    assert req["status"] == "pending"
    after = get_stay(client, owner, prop, stay["id"])
    assert after["bed_id"] == beds[0]["id"] and len(after["assignments"]) == 1


def test_tenant_cancel_and_scoping(client, world):
    owner, prop, beds = make(world)
    t1, t2 = world.tenant(), world.tenant("b")
    stay = reserve(client, owner, prop, t1, beds[0]["id"], days(10))
    rid = ask_move(client, t1, stay["id"], beds[1]["id"]).json()["id"]
    assert client.get(f"{TP}/bed-change-requests/{rid}", headers=t2["headers"]).status_code == 404
    assert client.post(f"{TP}/bed-change-requests/{rid}/cancel", headers=t2["headers"]).status_code == 404
    assert client.get(f"{TP}/bed-change-requests", headers=t2["headers"]).json()["total"] == 0
    res = client.post(f"{TP}/bed-change-requests/{rid}/cancel", headers=t1["headers"])
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    assert client.post(bc(prop, rid, "approve"), headers=owner["headers"]).status_code == 409


def test_finished_stays_cannot_request_moves_and_pending_move_is_cancelled(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10))
    rid = ask_move(client, tenant, stay["id"], beds[1]["id"]).json()["id"]
    stay_action(client, owner, prop, stay["id"], "cancel")
    got = client.get(f"{TP}/bed-change-requests/{rid}", headers=tenant["headers"]).json()
    assert got["status"] == "cancelled"
    assert ask_move(client, tenant, stay["id"], beds[1]["id"]).status_code == 409


def test_owner_scoping_of_bed_change_requests(client, world):
    owner, prop, beds = make(world)
    stranger = world.owner("x")
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(10))
    rid = ask_move(client, tenant, stay["id"], beds[1]["id"]).json()["id"]
    assert client.post(bc(prop, rid, "approve"), headers=stranger["headers"]).status_code == 404
    other_prop = world.property(owner)
    other_url = f"{pp(other_prop)}/bed-change-requests/{rid}/approve"
    assert client.post(other_url, headers=owner["headers"]).status_code == 404
    page = client.get(f"{pp(prop)}/bed-change-requests", headers=owner["headers"]).json()
    assert page["total"] == 1 and page["items"][0]["tenant_full_name"] == "Tina Tenant"
