import pytest

from tests.conftest import days
from tests.flows import TP, book, pp, reserve, stay_action

ZERO = "00000000-0000-4000-8000-000000000000"


def make(world):
    owner = world.owner()
    prop = world.property(owner, rooms=1, beds_per_room=2)
    return owner, prop, prop["beds"]


TENANT_ENDPOINTS = [
    ("get", f"{TP}/booking-requests"),
    ("get", f"{TP}/booking-requests/{ZERO}"),
    ("post", f"{TP}/booking-requests/{ZERO}/cancel"),
    ("get", f"{TP}/stays"),
    ("get", f"{TP}/stays/{ZERO}"),
    ("get", f"{TP}/bed-change-requests"),
    ("get", f"{TP}/bed-change-requests/{ZERO}"),
    ("post", f"{TP}/bed-change-requests/{ZERO}/cancel"),
]


def owner_endpoints(pid):
    base = f"/api/v1/properties/{pid}"
    return [
        ("get", f"{base}/booking-requests"),
        ("get", f"{base}/booking-requests/{ZERO}"),
        ("post", f"{base}/booking-requests/{ZERO}/approve"),
        ("post", f"{base}/booking-requests/{ZERO}/reject"),
        ("get", f"{base}/stays"),
        ("get", f"{base}/stays/{ZERO}"),
        ("post", f"{base}/stays/{ZERO}/check-in"),
        ("post", f"{base}/stays/{ZERO}/check-out"),
        ("post", f"{base}/stays/{ZERO}/cancel"),
        ("post", f"{base}/stays/{ZERO}/no-show"),
        ("get", f"{base}/bed-change-requests"),
        ("get", f"{base}/bed-change-requests/{ZERO}"),
        ("post", f"{base}/bed-change-requests/{ZERO}/approve"),
        ("post", f"{base}/bed-change-requests/{ZERO}/reject"),
    ]


@pytest.mark.parametrize("method,url", TENANT_ENDPOINTS)
def test_tenant_endpoints_reject_anonymous_and_owner_tokens(client, world, method, url):
    owner = world.owner()
    assert getattr(client, method)(url).status_code == 401
    assert getattr(client, method)(url, headers=owner["headers"]).status_code == 401


def test_tenant_post_endpoints_reject_anonymous_and_owner(client, world):
    owner = world.owner()
    for url, body in (
        (f"{TP}/booking-requests", {"bed_id": ZERO, "planned_check_in": days(5)}),
        (f"{TP}/bed-change-requests", {"stay_id": ZERO, "to_bed_id": ZERO}),
    ):
        assert client.post(url, json=body).status_code == 401
        assert client.post(url, json=body, headers=owner["headers"]).status_code == 401


def test_owner_endpoints_reject_anonymous_and_tenant_tokens(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    for method, url in owner_endpoints(prop["id"]):
        assert getattr(client, method)(url).status_code == 401, url
        assert getattr(client, method)(url, headers=tenant["headers"]).status_code == 401, url


def test_tenant_cannot_perform_owner_lifecycle_actions(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(0))
    for action in ("check-in", "check-out", "cancel", "no-show"):
        res = client.post(f"{pp(prop)}/stays/{stay['id']}/{action}", headers=tenant["headers"])
        assert res.status_code == 401
    assert client.get(f"{pp(prop)}/stays/{stay['id']}", headers=owner["headers"]).json()["status"] == "reserved"


def test_other_owner_cannot_touch_properties_stays_or_requests(client, world):
    owner, prop, beds = make(world)
    stranger = world.owner("stranger")
    tenant = world.tenant()
    stay = reserve(client, owner, prop, tenant, beds[0]["id"], days(0))
    pending = book(client, world.tenant("p"), beds[1]["id"], days(10)).json()["id"]
    h = stranger["headers"]
    base = pp(prop)
    assert client.get(f"{base}/stays", headers=h).status_code == 404
    assert client.get(f"{base}/stays/{stay['id']}", headers=h).status_code == 404
    for action in ("check-in", "check-out", "cancel", "no-show"):
        assert stay_action(client, stranger, prop, stay["id"], action).status_code == 404
    assert client.post(f"{base}/booking-requests/{pending}/approve", headers=h).status_code == 404
    assert client.post(f"{base}/booking-requests/{pending}/reject", headers=h).status_code == 404
    # owner's own state is untouched
    assert client.get(f"{base}/stays/{stay['id']}", headers=owner["headers"]).json()["status"] == "reserved"


def test_tenant_token_version_still_enforced_on_new_endpoints(client, world):
    tenant = world.tenant()
    assert client.get(f"{TP}/stays", headers=tenant["headers"]).status_code == 200
    res = client.post(
        "/api/v1/tenant-auth/me/password",
        json={"current_password": world.PASSWORD, "new_password": "An0ther-passw0rd!"},
        headers=tenant["headers"],
    )
    assert res.status_code == 200, res.text
    assert client.get(f"{TP}/stays", headers=tenant["headers"]).status_code == 401


def test_error_bodies_do_not_leak_internals(client, world):
    owner, prop, beds = make(world)
    tenant = world.tenant()
    reserve(client, owner, prop, tenant, beds[0]["id"], days(10), days(20))
    res = book(client, world.tenant("b"), beds[0]["id"], days(12), days(14))
    assert res.status_code == 409
    text_body = res.text.lower()
    for word in ("traceback", "sqlalchemy", "psycopg", "constraint", "select ", "tina"):
        assert word not in text_body
