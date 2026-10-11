"""Reusable request flows for Module 6 tests."""

from tests.conftest import days

TP = "/api/v1/tenant-portal"


def pp(prop: dict) -> str:
    return f"/api/v1/properties/{prop['id']}"


def book(client, tenant, bed_id, check_in=None, check_out=None, **extra):
    body = {"bed_id": bed_id, "planned_check_in": check_in or days(10)}
    if check_out:
        body["planned_check_out"] = check_out
    body.update(extra)
    return client.post(f"{TP}/booking-requests", json=body, headers=tenant["headers"])


def approve(client, owner, prop, request_id):
    return client.post(
        f"{pp(prop)}/booking-requests/{request_id}/approve", headers=owner["headers"]
    )


def reject(client, owner, prop, request_id, reason=None):
    return client.post(
        f"{pp(prop)}/booking-requests/{request_id}/reject",
        json={"reason": reason} if reason else {},
        headers=owner["headers"],
    )


def reserve(client, owner, prop, tenant, bed_id, check_in=None, check_out=None):
    """Book + approve; returns the stay JSON."""
    res = book(client, tenant, bed_id, check_in, check_out)
    assert res.status_code == 201, res.text
    res = approve(client, owner, prop, res.json()["id"])
    assert res.status_code == 200, res.text
    return res.json()


def stay_action(client, owner, prop, stay_id, action, body=None):
    return client.post(
        f"{pp(prop)}/stays/{stay_id}/{action}", json=body or {}, headers=owner["headers"]
    )


def get_stay(client, owner, prop, stay_id):
    res = client.get(f"{pp(prop)}/stays/{stay_id}", headers=owner["headers"])
    assert res.status_code == 200, res.text
    return res.json()


def ask_move(client, tenant, stay_id, to_bed_id, reason=None):
    return client.post(
        f"{TP}/bed-change-requests",
        json={"stay_id": stay_id, "to_bed_id": to_bed_id, "reason": reason},
        headers=tenant["headers"],
    )


def availability(client, prop, check_in, check_out=None):
    params = {"check_in": check_in}
    if check_out:
        params["check_out"] = check_out
    return client.get(f"/api/v1/public/properties/{prop['id']}/availability", params=params)


def bed_available(client, prop, bed_id, check_in, check_out=None) -> bool:
    res = availability(client, prop, check_in, check_out)
    assert res.status_code == 200, res.text
    for room in res.json()["rooms"]:
        for bed in room["beds"]:
            if bed["bed_id"] == bed_id:
                return bed["available"]
    raise AssertionError("bed not listed")
