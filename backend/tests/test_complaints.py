"""Module 10: complaints & issue tracking (visibility, lifecycle, audit)."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import days
from tests.flows import TP, pp, reserve, stay_action

URL = f"{TP}/complaints"


class Ctx:
    """One owner with property A (and optionally B) and helpers for tenants."""

    def __init__(self, client, world, beds=8):
        self.client, self.world = client, world
        self.owner = world.owner()
        self.prop = world.property(self.owner, rooms=1, beds_per_room=beds)
        self._beds = {}

    def stay(self, state="checked_in", prop=None, tenant=None, owner=None):
        prop = prop or self.prop
        owner = owner or self.owner
        tenant = tenant or self.world.tenant()
        used = self._beds.setdefault(prop["id"], 0)
        self._beds[prop["id"]] = used + 1
        stay = reserve(self.client, owner, prop, tenant, prop["beds"][used]["id"], days(0))
        steps = {"reserved": [], "checked_in": ["check-in"], "checked_out": ["check-in", "check-out"],
                 "cancelled": ["cancel"]}[state]
        for action in steps:
            res = stay_action(self.client, owner, prop, stay["id"], action,
                              {"reason": "t"} if action == "cancel" else None)
            assert res.status_code == 200, res.text
        return tenant, stay

    def resident(self, prop=None, owner=None):
        return self.stay("checked_in", prop, owner=owner)

    def raise_(self, tenant, stay, visibility="public", title="Leaking tap", category="plumbing"):
        return self.client.post(URL, headers=tenant["headers"], json={
            "stay_id": stay["id"], "category": category, "title": title,
            "description": "Water everywhere", "visibility": visibility})

    def make(self, visibility="public", **kw):
        tenant, stay = self.resident()
        res = self.raise_(tenant, stay, visibility, **kw)
        assert res.status_code == 201, res.text
        return tenant, stay, res.json()

    def owner_status(self, complaint, status, note=None, owner=None, prop=None):
        body = {"status": status}
        if note:
            body["note"] = note
        return self.client.post(
            f"{pp(prop or self.prop)}/complaints/{complaint['id']}/status", json=body,
            headers=(owner or self.owner)["headers"])

    def tenant_action(self, tenant, complaint, action, note=None):
        return self.client.post(f"{URL}/{complaint['id']}/{action}", headers=tenant["headers"],
                                json={"note": note} if note else {})

    def get(self, url, who, **params):
        res = self.client.get(url, params=params, headers=who["headers"])
        assert res.status_code == 200, res.text
        return res.json()

    def status_of(self, complaint):
        return self.get(f"{pp(self.prop)}/complaints/{complaint['id']}", self.owner)["status"]


@pytest.fixture()
def ctx(client, world):
    return Ctx(client, world)


def ids(page):
    return {i["id"] for i in page["items"]}


# ----------------------------------------------------------------- creating


def test_create_public_and_private_starts_open_with_initial_history(ctx, db_session):
    for visibility in ("public", "private"):
        tenant, stay, c = ctx.make(visibility)
        assert c["status"] == "open" and c["visibility"] == visibility
        assert c["raised_by_me"] is True and c["stay_id"] == stay["id"]
        assert c["property_id"] == ctx.prop["id"] and c["awaiting_my_confirmation"] is False
        (h,) = ctx.get(f"{URL}/{c['id']}/history", tenant)
        assert h["from_status"] is None and h["to_status"] == "open"
        assert h["actor_type"] == "tenant" and h["by_me"] is True
    rows = db_session.execute(text(
        "select from_status, to_status, actor_type, actor_tenant_id is not null, actor_owner_id is null "
        "from complaint_status_history")).all()
    assert rows == [(None, "open", "tenant", True, True)] * 2


@pytest.mark.parametrize("override", [
    {"category": "food"}, {"category": ""}, {"visibility": "friends"}, {"visibility": None},
    {"title": ""}, {"title": "   "}, {"title": "x" * 201}, {"description": ""},
    {"description": "x" * 4001}, {"status": "closed"}, {"tenant_id": str(uuid.uuid4())},
    {"property_id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())},
])
def test_invalid_creation_payloads(ctx, override):
    tenant, stay = ctx.resident()
    body = {"stay_id": stay["id"], "category": "other", "title": "t",
            "description": "d", "visibility": "public", **override}
    res = ctx.client.post(URL, json=body, headers=tenant["headers"])
    assert res.status_code == 422, override


@pytest.mark.parametrize("state", ["reserved", "checked_out", "cancelled"])
def test_only_a_checked_in_stay_can_raise_complaints(ctx, state):
    tenant, stay = ctx.stay(state)
    assert ctx.raise_(tenant, stay).status_code == 409


def test_cannot_use_someone_elses_or_unknown_stay(ctx):
    _, stay = ctx.resident()
    stranger = ctx.world.tenant()
    assert ctx.raise_(stranger, stay).status_code == 404
    assert ctx.raise_(stranger, {"id": str(uuid.uuid4())}).status_code == 404


# ---------------------------------------------------------------- visibility


def test_public_visible_to_checked_in_tenants_of_same_property(ctx):
    author, _, c = ctx.make("public")
    neighbour, _ = ctx.resident()
    page = ctx.get(URL, neighbour)
    assert ids(page) == {c["id"]} and page["total"] == 1
    seen = ctx.get(f"{URL}/{c['id']}", neighbour)
    assert seen["raised_by_me"] is False and seen["stay_id"] is None
    assert seen["awaiting_my_confirmation"] is False
    assert "tenant_id" not in seen and "tenant_full_name" not in seen
    (h,) = ctx.get(f"{URL}/{c['id']}/history", neighbour)
    assert h["by_me"] is False and "actor_tenant_id" not in h
    assert ctx.get(f"{URL}/summary", neighbour)["total"] == 1
    assert ids(ctx.get(URL, neighbour, mine="true")) == set()
    assert ids(ctx.get(URL, author, mine="true")) == {c["id"]}


def test_public_not_visible_to_tenants_of_another_property(client, world):
    ctx = Ctx(client, world)
    other_prop = world.property(ctx.owner, rooms=1, beds_per_room=2)
    _, _, c = ctx.make("public")
    outsider, _ = ctx.resident(prop=other_prop)
    assert ctx.get(URL, outsider)["items"] == []
    assert ctx.get(f"{URL}/summary", outsider)["total"] == 0
    assert client.get(f"{URL}/{c['id']}", headers=outsider["headers"]).status_code == 404
    assert client.get(f"{URL}/{c['id']}/history", headers=outsider["headers"]).status_code == 404
    assert ctx.get(URL, outsider, property_id=ctx.prop["id"])["items"] == []
    # a tenant of a different owner's property
    foreign = Ctx(client, world)
    stranger, _ = foreign.resident()
    assert client.get(f"{URL}/{c['id']}", headers=stranger["headers"]).status_code == 404


@pytest.mark.parametrize("state", ["reserved", "checked_out", "cancelled"])
def test_unrelated_reservation_or_past_stay_is_not_membership(ctx, state):
    _, _, c = ctx.make("public")
    outsider, _ = ctx.stay(state)
    assert ctx.get(URL, outsider)["items"] == []
    assert ctx.client.get(f"{URL}/{c['id']}", headers=outsider["headers"]).status_code == 404
    assert ctx.get(f"{URL}/summary", outsider)["total"] == 0


def test_membership_requires_a_stay_not_just_a_booking_request(ctx):
    from tests.flows import book

    _, _, c = ctx.make("public")
    requester = ctx.world.tenant()
    assert book(ctx.client, requester, ctx.prop["beds"][7]["id"], days(5)).status_code == 201
    assert ctx.get(URL, requester)["items"] == []
    assert ctx.client.get(f"{URL}/{c['id']}", headers=requester["headers"]).status_code == 404


def test_reporter_keeps_access_after_checkout_but_loses_nothing_to_others(ctx):
    tenant, stay, c = ctx.make("private")
    assert stay_action(ctx.client, ctx.owner, ctx.prop, stay["id"], "check-out").status_code == 200
    assert ctx.get(f"{URL}/{c['id']}", tenant)["raised_by_me"] is True


def test_private_complaints_never_leak_to_other_tenants_anywhere(ctx):
    author, _, private = ctx.make("private", title="Secret harassment", category="safety")
    _, _, public = ctx.make("public", title="Public wifi")
    peer, _ = ctx.resident()  # same property, checked in
    assert ids(ctx.get(URL, peer)) == {public["id"]}
    assert ctx.get(URL, peer)["total"] == 1
    # filters cannot surface it
    for params in ({"visibility": "private"}, {"category": "safety"}, {"status_filter": "open"},
                   {"property_id": ctx.prop["id"]}, {"mine": "false"}, {"limit": 100}):
        page = ctx.get(URL, peer, **params)
        assert private["id"] not in ids(page), params
    assert ctx.get(URL, peer, visibility="private")["total"] == 0
    assert ctx.get(URL, peer, category="safety")["total"] == 0
    # counts and summaries
    summary = ctx.get(f"{URL}/summary", peer)
    assert summary["total"] == 1 and summary["by_status"]["open"] == 1
    assert ctx.get(f"{URL}/summary", peer, property_id=ctx.prop["id"])["total"] == 1
    assert ctx.get(f"{URL}/summary", author)["total"] == 2
    # direct object access: detail, history, actions
    assert ctx.client.get(f"{URL}/{private['id']}", headers=peer["headers"]).status_code == 404
    assert ctx.client.get(f"{URL}/{private['id']}/history", headers=peer["headers"]).status_code == 404
    for action in ("confirm-resolution", "reject-resolution"):
        assert ctx.tenant_action(peer, private, action).status_code == 404
    assert ctx.get(f"{URL}/{private['id']}", author)["title"] == "Secret harassment"
    # not even after the owner resolves it
    ctx.owner_status(private, "in_progress")
    ctx.owner_status(private, "resolved")
    assert ctx.get(URL, peer)["total"] == 1
    assert ctx.get(f"{URL}/summary", peer)["awaiting_confirmation"] == 0


def test_summary_counts_by_status_match_visible_complaints(ctx):
    author, stay, a = ctx.make("public")
    _, _, b = ctx.make("public")
    _, _, c = ctx.make("private")
    ctx.owner_status(a, "in_progress")
    ctx.owner_status(a, "resolved")
    ctx.owner_status(b, "in_progress")
    peer, _ = ctx.resident()
    assert ctx.get(f"{URL}/summary", peer) == {
        "total": 2, "awaiting_confirmation": 1,
        "by_status": {"open": 0, "in_progress": 1, "resolved": 1, "closed": 0}}
    assert ctx.get(f"{URL}/summary", author)["awaiting_confirmation"] == 1
    owner_summary = ctx.get(f"{pp(ctx.prop)}/complaints/summary", ctx.owner)
    assert owner_summary["total"] == 3 and owner_summary["by_status"]["open"] == 1


def test_other_tenants_are_read_only(ctx):
    author, _, c = ctx.make("public")
    ctx.owner_status(c, "in_progress")
    ctx.owner_status(c, "resolved")
    peer, _ = ctx.resident()
    for action in ("confirm-resolution", "reject-resolution"):
        assert ctx.tenant_action(peer, c, action).status_code == 403
    assert ctx.status_of(c) == "resolved"
    for method in ("PATCH", "PUT", "DELETE"):
        res = ctx.client.request(method, f"{URL}/{c['id']}", json={"status": "closed"},
                                 headers=peer["headers"])
        assert res.status_code == 405
    assert ctx.get(f"{URL}/{c['id']}", peer)["awaiting_my_confirmation"] is False
    assert ctx.get(f"{URL}/{c['id']}", author)["awaiting_my_confirmation"] is True


# --------------------------------------------------------------------- owner


def test_owner_sees_and_manages_only_owned_properties(client, world):
    a, b = Ctx(client, world), Ctx(client, world)
    _, _, public = a.make("public")
    _, _, private = a.make("private")
    page = a.get(f"{pp(a.prop)}/complaints", a.owner)
    assert ids(page) == {public["id"], private["id"]}
    item = next(i for i in page["items"] if i["id"] == private["id"])
    assert item["tenant_full_name"] == "Tina Tenant" and item["awaiting_tenant_confirmation"] is False
    assert a.get(f"{pp(a.prop)}/complaints", a.owner, visibility="private")["total"] == 1
    for url in (f"{pp(a.prop)}/complaints", f"{pp(a.prop)}/complaints/summary",
                f"{pp(a.prop)}/complaints/{public['id']}",
                f"{pp(a.prop)}/complaints/{public['id']}/history"):
        assert client.get(url, headers=b.owner["headers"]).status_code == 404, url
    # right owner, wrong property; wrong owner, right property
    assert client.get(f"{pp(b.prop)}/complaints/{public['id']}",
                      headers=b.owner["headers"]).status_code == 404
    assert b.owner_status(public, "in_progress", owner=b.owner, prop=a.prop).status_code == 404
    assert b.owner_status(public, "in_progress", owner=b.owner, prop=b.prop).status_code == 404
    assert a.status_of(public) == "open"
    assert b.get(f"{pp(b.prop)}/complaints", b.owner)["items"] == []


def test_authentication_and_role_separation(ctx):
    tenant, _, c = ctx.make()
    owner_urls = [f"{pp(ctx.prop)}/complaints", f"{pp(ctx.prop)}/complaints/summary",
                  f"{pp(ctx.prop)}/complaints/{c['id']}", f"{pp(ctx.prop)}/complaints/{c['id']}/history"]
    for url in [URL, f"{URL}/summary", f"{URL}/{c['id']}", f"{URL}/{c['id']}/history"] + owner_urls:
        assert ctx.client.get(url).status_code == 401, url
    for url in [URL, f"{URL}/summary"]:
        assert ctx.client.get(url, headers=ctx.owner["headers"]).status_code == 401
    for url in owner_urls:
        assert ctx.client.get(url, headers=tenant["headers"]).status_code == 401
    assert ctx.client.post(f"{pp(ctx.prop)}/complaints/{c['id']}/status", json={"status": "in_progress"},
                           headers=tenant["headers"]).status_code == 401
    assert ctx.client.post(f"{URL}/{c['id']}/confirm-resolution",
                           headers=ctx.owner["headers"]).status_code == 401


# ----------------------------------------------------------------- lifecycle


def test_full_lifecycle_with_rejection_and_confirmation(ctx, db_session):
    tenant, _, c = ctx.make("public", title="Broken fan", category="electrical")
    assert ctx.owner_status(c, "in_progress", "On it").json()["status"] == "in_progress"
    res = ctx.owner_status(c, "resolved", "Replaced the fan")
    assert res.json()["status"] == "resolved" and res.json()["awaiting_tenant_confirmation"] is True
    assert ctx.get(f"{URL}/{c['id']}", tenant)["awaiting_my_confirmation"] is True
    # reporter says it's not fixed
    res = ctx.tenant_action(tenant, c, "reject-resolution", "Still wobbling")
    assert res.status_code == 200 and res.json()["status"] == "in_progress"
    assert res.json()["awaiting_my_confirmation"] is False
    # owner continues and resolves again; reporter confirms
    assert ctx.owner_status(c, "resolved", "Tightened the mount").status_code == 200
    res = ctx.tenant_action(tenant, c, "confirm-resolution", "All good now")
    assert res.status_code == 200 and res.json()["status"] == "closed"

    history = ctx.get(f"{pp(ctx.prop)}/complaints/{c['id']}/history", ctx.owner)
    assert [(h["from_status"], h["to_status"], h["actor_type"], h["note"]) for h in history] == [
        (None, "open", "tenant", None),
        ("open", "in_progress", "owner", "On it"),
        ("in_progress", "resolved", "owner", "Replaced the fan"),
        ("resolved", "in_progress", "tenant", "Still wobbling"),
        ("in_progress", "resolved", "owner", "Tightened the mount"),
        ("resolved", "closed", "tenant", "All good now"),
    ]
    for h in history:
        assert (h["actor_tenant_id"] is not None) == (h["actor_type"] == "tenant")
        assert (h["actor_owner_id"] is not None) == (h["actor_type"] == "owner")
    assert history[1]["actor_owner_id"] == ctx.owner["id"]
    assert history[0]["actor_tenant_id"] == tenant["id"]
    assert [h["created_at"] for h in history] == sorted(h["created_at"] for h in history)
    # the original content is untouched by status changes
    final = ctx.get(f"{URL}/{c['id']}", tenant)
    for key in ("title", "description", "category", "visibility", "property_id"):
        assert final[key] == c[key], key
    # complaint status always equals the last history row
    last = db_session.execute(text(
        "select to_status from complaint_status_history order by created_at desc, id limit 1")).scalar()
    assert last == "closed"


def test_owner_cannot_close_directly_from_any_state(ctx):
    tenant, _, c = ctx.make()
    for _ in range(3):
        res = ctx.owner_status(c, "closed")
        assert res.status_code == 409 and "cannot close" in res.json()["detail"]
        assert ctx.status_of(c) in ("open", "in_progress", "resolved")
        # advance one step
        nxt = {"open": "in_progress", "in_progress": "resolved"}.get(ctx.status_of(c))
        if nxt:
            assert ctx.owner_status(c, nxt).status_code == 200
    assert ctx.status_of(c) == "resolved"
    assert ctx.owner_status(c, "closed").status_code == 409
    assert ctx.status_of(c) == "resolved"


def test_cannot_close_before_resolved(ctx):
    tenant, _, c = ctx.make()
    for state_steps in (None, "in_progress"):
        if state_steps:
            ctx.owner_status(c, state_steps)
        for action in ("confirm-resolution", "reject-resolution"):
            res = ctx.tenant_action(tenant, c, action)
            assert res.status_code == 409 and "resolved" in res.json()["detail"], action
    assert ctx.status_of(c) == "in_progress"
    assert len(ctx.get(f"{URL}/{c['id']}/history", tenant)) == 2


def test_invalid_and_repeated_transitions_are_rejected(ctx):
    tenant, _, c = ctx.make()
    assert ctx.owner_status(c, "resolved").status_code == 409      # skip in_progress
    assert ctx.owner_status(c, "open").status_code == 409
    assert ctx.owner_status(c, "in_progress").status_code == 200
    assert ctx.owner_status(c, "in_progress").status_code == 409   # repeat
    assert ctx.owner_status(c, "open").status_code == 409          # backwards
    assert ctx.owner_status(c, "resolved").status_code == 200
    assert ctx.owner_status(c, "resolved").status_code == 409      # repeat
    assert ctx.owner_status(c, "in_progress").status_code == 403   # reopening is the tenant's call
    assert ctx.owner_status(c, "bogus").status_code == 422
    assert ctx.tenant_action(tenant, c, "confirm-resolution").status_code == 200
    # closed is terminal for everybody
    for status in ("open", "in_progress", "resolved", "closed"):
        assert ctx.owner_status(c, status).status_code == 409, status
    for action in ("confirm-resolution", "reject-resolution"):
        assert ctx.tenant_action(tenant, c, action).status_code == 409
    assert ctx.status_of(c) == "closed"
    assert len(ctx.get(f"{URL}/{c['id']}/history", tenant)) == 4  # no extra rows from the rejected attempts


def test_only_the_original_tenant_can_confirm_or_reject(ctx):
    author, _, c = ctx.make("public")
    peer, _ = ctx.resident()
    ctx.owner_status(c, "in_progress")
    ctx.owner_status(c, "resolved")
    for action in ("confirm-resolution", "reject-resolution"):
        assert ctx.tenant_action(peer, c, action, "nope").status_code == 403
    assert ctx.status_of(c) == "resolved"
    assert ctx.tenant_action(author, c, "reject-resolution", "not yet").status_code == 200
    assert ctx.status_of(c) == "in_progress"
    assert len(ctx.get(f"{URL}/{c['id']}/history", author)) == 4


def test_resolution_note_validation(ctx):
    tenant, _, c = ctx.make()
    ctx.owner_status(c, "in_progress")
    ctx.owner_status(c, "resolved")
    for bad in ({"note": ""}, {"note": "x" * 501}, {"status": "closed"}, {"stay_id": str(uuid.uuid4())}):
        res = ctx.client.post(f"{URL}/{c['id']}/confirm-resolution", json=bad, headers=tenant["headers"])
        assert res.status_code == 422, bad
    assert ctx.status_of(c) == "resolved"


# ------------------------------------------------------ audit & consistency


def test_history_cannot_be_modified_through_the_api(ctx):
    tenant, _, c = ctx.make()
    for url, headers in ((f"{URL}/{c['id']}/history", tenant["headers"]),
                         (f"{pp(ctx.prop)}/complaints/{c['id']}/history", ctx.owner["headers"])):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            res = ctx.client.request(method, url, json={"note": "x"}, headers=headers)
            assert res.status_code == 405, (method, url)
    for method in ("PATCH", "PUT", "DELETE"):
        base = f"{pp(ctx.prop)}/complaints/{c['id']}"
        assert ctx.client.request(method, base, json={"title": "x"},
                                  headers=ctx.owner["headers"]).status_code == 405


def test_database_protects_history_and_immutable_fields(ctx, db_session):
    tenant, _, c = ctx.make("private")
    for sql in ("update complaint_status_history set note='tampered'",
                "delete from complaint_status_history"):
        with pytest.raises(DBAPIError):
            db_session.execute(text(sql))
            db_session.commit()
        db_session.rollback()
    for column, value in (("title", "'changed'"), ("description", "'changed'"),
                          ("category", "'noise'"), ("visibility", "'public'"),
                          ("stay_id", "gen_random_uuid()")):
        with pytest.raises(DBAPIError):
            db_session.execute(text(f"update complaints set {column} = {value}"))
            db_session.commit()
        db_session.rollback()
    db_session.execute(text("update complaints set status='in_progress'"))  # status may move
    db_session.commit()
    assert db_session.execute(text("select visibility from complaints")).scalar() == "private"


def test_database_enforces_the_lifecycle_and_actors(ctx, db_session):
    tenant, _, c = ctx.make()
    owner_id = ctx.owner["id"]

    def insert(frm, to, actor_type, tenant_id=None, owner_id_=None):
        return db_session.execute(text(
            "insert into complaint_status_history (id, complaint_id, from_status, to_status, "
            "actor_type, actor_tenant_id, actor_owner_id) values (gen_random_uuid(), :c, :f, :t, :a, :ti, :oi)"),
            {"c": c["id"], "f": frm, "t": to, "a": actor_type, "ti": tenant_id, "oi": owner_id_})

    bad = [
        ("open", "closed", "owner", None, owner_id),          # owner closing
        ("in_progress", "closed", "tenant", tenant["id"], None),
        ("resolved", "closed", "owner", None, owner_id),
        ("open", "resolved", "owner", None, owner_id),         # skipping a step
        ("resolved", "in_progress", "owner", None, owner_id),  # only the tenant reopens
        (None, "open", "tenant", tenant["id"], None),          # second initial record
        ("open", "in_progress", "owner", tenant["id"], None),  # actor id/type mismatch
        ("open", "in_progress", "owner", None, None),
    ]
    for args in bad:
        with pytest.raises(IntegrityError):
            insert(*args)
            db_session.commit()
        db_session.rollback()
    insert("open", "in_progress", "owner", None, owner_id)  # a legal one is accepted
    db_session.commit()


def test_status_and_history_commit_together_or_not_at_all(ctx, db_session, monkeypatch):
    from app.services import complaint_service

    tenant, _, c = ctx.make()
    original = complaint_service._record

    def failing(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("boom after writing the history row")

    monkeypatch.setattr(complaint_service, "_record", failing)
    client_ = ctx.client
    with pytest.raises(RuntimeError):
        client_.post(f"{pp(ctx.prop)}/complaints/{c['id']}/status", json={"status": "in_progress"},
                     headers=ctx.owner["headers"])
    monkeypatch.undo()
    assert ctx.status_of(c) == "open"
    assert db_session.execute(text("select count(*) from complaint_status_history")).scalar() == 1

    # a failed creation leaves neither a complaint nor a history row
    tenant2, stay2 = ctx.resident()
    monkeypatch.setattr(complaint_service, "_record", failing)
    with pytest.raises(RuntimeError):
        ctx.raise_(tenant2, stay2)
    monkeypatch.undo()
    assert db_session.execute(text("select count(*) from complaints")).scalar() == 1
    assert db_session.execute(text("select count(*) from complaint_status_history")).scalar() == 1


def test_every_complaint_status_matches_its_latest_history(ctx, db_session):
    tenant, _, c = ctx.make()
    ctx.owner_status(c, "in_progress")
    ctx.owner_status(c, "resolved")
    ctx.tenant_action(tenant, c, "reject-resolution")
    _, _, d = ctx.make()
    ctx.owner_status(d, "in_progress")
    mismatches = db_session.execute(text(
        "select count(*) from complaints c where c.status is distinct from ("
        "select h.to_status from complaint_status_history h where h.complaint_id=c.id "
        "order by h.created_at desc, h.id desc limit 1)")).scalar()
    assert mismatches == 0
    assert db_session.execute(text(
        "select count(*) from complaints c where (select count(*) from complaint_status_history h "
        "where h.complaint_id=c.id and h.from_status is null) <> 1")).scalar() == 0


def test_racing_decisions_have_one_winner_and_consistent_history(ctx, db_session):
    from app.db.session import SessionLocal
    from app.models.tenant import Tenant
    from app.services import complaint_service
    from app.services.errors import AppError

    for _ in range(3):
        tenant, _, c = ctx.make()
        ctx.owner_status(c, "in_progress")
        ctx.owner_status(c, "resolved")
        barrier = threading.Barrier(4)

        def attempt(action, tenant=tenant, c=c, barrier=barrier):
            session = SessionLocal()
            try:
                t = session.get(Tenant, uuid.UUID(tenant["id"]))
                barrier.wait(timeout=10)
                fn = (complaint_service.confirm_resolution if action == "confirm"
                      else complaint_service.reject_resolution)
                fn(session, t, uuid.UUID(c["id"]), None)
                return "ok"
            except AppError as exc:
                return exc.status_code
            finally:
                session.close()

        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(attempt, ["confirm", "reject", "confirm", "reject"]))
        assert results.count("ok") == 1 and results.count(409) == 3, results
        rows = db_session.execute(text(
            "select from_status, to_status from complaint_status_history where complaint_id=:c "
            "order by created_at, id"), {"c": c["id"]}).all()
        assert len(rows) == 4 and rows[-1][0] == "resolved"
        db_session.rollback()
        assert db_session.execute(text("select status from complaints where id=:c"),
                                  {"c": c["id"]}).scalar() == rows[-1][1]
        db_session.rollback()


def test_racing_owner_updates_apply_once(ctx, db_session):
    from app.db.session import SessionLocal
    from app.models.owner import Owner
    from app.services import complaint_service
    from app.services.errors import AppError

    _, _, c = ctx.make()
    barrier = threading.Barrier(3)

    def attempt(_):
        session = SessionLocal()
        try:
            o = session.get(Owner, uuid.UUID(ctx.owner["id"]))
            barrier.wait(timeout=10)
            complaint_service.change_status(
                session, o, uuid.UUID(ctx.prop["id"]), uuid.UUID(c["id"]), "in_progress", None)
            return "ok"
        except AppError as exc:
            return exc.status_code
        finally:
            session.close()

    with ThreadPoolExecutor(3) as pool:
        results = sorted(str(r) for r in pool.map(attempt, range(3)))
    assert results == ["409", "409", "ok"]
    assert db_session.execute(text("select count(*) from complaint_status_history")).scalar() == 2


# ------------------------------------------------------------------ paging


def test_pagination_ordering_and_filters(ctx):
    made = [ctx.make("public", category=cat)[2] for cat in ("noise", "noise", "safety", "other", "noise")]
    ctx.owner_status(made[0], "in_progress")
    reader, _ = ctx.resident()
    full = [i["id"] for i in ctx.get(URL, reader)["items"]]
    assert full == [m["id"] for m in reversed(made)]  # newest first
    seen = []
    for offset in (0, 2, 4):
        page = ctx.get(URL, reader, limit=2, offset=offset)
        assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == offset
        seen += [i["id"] for i in page["items"]]
    assert seen == full
    assert ctx.get(URL, reader, category="noise")["total"] == 3
    assert ctx.get(URL, reader, status_filter="in_progress")["total"] == 1
    assert ctx.get(f"{pp(ctx.prop)}/complaints", ctx.owner, category="safety")["total"] == 1
    assert ctx.get(f"{pp(ctx.prop)}/complaints", ctx.owner, limit=2)["total"] == 5
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"category": "food"},
                {"visibility": "all"}, {"status_filter": "done"}):
        assert ctx.client.get(URL, params=bad, headers=reader["headers"]).status_code == 422, bad
