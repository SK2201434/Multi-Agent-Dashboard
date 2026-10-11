"""Smoke-level regression of Modules 1-5 through their public API."""

from tests.conftest import days
from tests.flows import pp


def test_owner_auth_and_property_room_bed_flow(client, world):
    owner = world.owner()
    prop = world.property(owner, rooms=2, beds_per_room=2)
    base = pp(prop)

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/properties", headers=owner["headers"]).json()[0]["id"] == prop["id"]
    assert client.get(base, headers=owner["headers"]).status_code == 200
    assert client.get("/api/v1/properties").status_code == 401

    rooms = client.get(f"{base}/rooms", headers=owner["headers"]).json()
    assert len(rooms) == 2
    room_id = rooms[0]["id"]
    beds = client.get(f"{base}/rooms/{room_id}/beds", headers=owner["headers"]).json()
    assert len(beds) == 2

    res = client.post(f"{base}/rooms/{room_id}/beds", json={"bed_label": "Z9"}, headers=owner["headers"])
    assert res.status_code == 201
    res = client.patch(
        f"{base}/rooms/{room_id}/beds/{res.json()['id']}",
        json={"status": "maintenance"}, headers=owner["headers"])
    assert res.json()["status"] == "maintenance"

    assert client.patch(base, json={"description": "nice"}, headers=owner["headers"]).status_code == 200
    public = client.get(f"/api/v1/public/properties/{prop['id']}")
    assert public.status_code == 200 and "owner_id" not in public.json()
    assert client.get("/api/v1/public/properties/nearby?latitude=17&longitude=78").status_code == 200

    other = world.owner("other")
    assert client.get(base, headers=other["headers"]).status_code == 404


def test_tenant_account_flow(client, world):
    tenant = world.tenant()
    me = client.get("/api/v1/tenant-auth/me", headers=tenant["headers"])
    assert me.status_code == 200 and me.json()["email"] == tenant["email"]
    res = client.patch("/api/v1/tenant-auth/me", json={"full_name": "Tina T"}, headers=tenant["headers"])
    assert res.status_code == 200 and res.json()["full_name"] == "Tina T"


def test_owner_created_tenant_activation_and_association(client, world):
    owner = world.owner()
    prop = world.property(owner)
    res = client.post(
        f"{pp(prop)}/tenants",
        json={"full_name": "Pat Pending", "email": "pat@example.com"},
        headers=owner["headers"],
    )
    assert res.status_code == 201, res.text
    tenant_id = res.json()["tenant_id"]
    assert res.json()["account_status"] == "pending_activation"
    listing = client.get(f"{pp(prop)}/tenants", headers=owner["headers"]).json()
    assert [t["tenant_id"] for t in listing] == [tenant_id]
    res = client.delete(f"{pp(prop)}/tenants/{tenant_id}", headers=owner["headers"])
    assert res.status_code == 200 and res.json()["outcome"] == "invitation_cancelled"


def test_tenant_token_rejected_on_owner_routes_and_vice_versa(client, world):
    owner, tenant = world.owner(), world.tenant()
    assert client.get("/api/v1/properties", headers=tenant["headers"]).status_code == 401
    assert client.get("/api/v1/tenant-auth/me", headers=owner["headers"]).status_code == 401
    assert days(0)
