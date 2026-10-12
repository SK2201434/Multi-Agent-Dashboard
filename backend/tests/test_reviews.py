"""Module 9: reviews & ratings."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import days
from tests.flows import TP, book, pp, reserve, stay_action

REVIEWS = f"{TP}/reviews"


class Ctx:
    def __init__(self, client, world, beds=10):
        self.client, self.world = client, world
        self.owner = world.owner()
        self.prop = world.property(self.owner, rooms=1, beds_per_room=beds)
        self._bed = 0

    def bed(self):
        bed = self.prop["beds"][self._bed]["id"]
        self._bed += 1
        return bed

    def stay(self, tenant=None, state="checked_out", prop=None, bed=None):
        """Reserve (and optionally check in/out) a stay. state in
        reserved / checked_in / checked_out / cancelled / no_show."""
        tenant = tenant or self.world.tenant()
        prop = prop or self.prop
        bed = bed or (self.bed() if prop is self.prop else prop["beds"][0]["id"])
        stay = reserve(self.client, self.owner, prop, tenant, bed, days(0))
        steps = {"reserved": [], "checked_in": ["check-in"],
                 "checked_out": ["check-in", "check-out"],
                 "cancelled": ["cancel"], "no_show": ["no-show"]}[state]
        for action in steps:
            res = stay_action(self.client, self.owner, prop, stay["id"], action,
                              {"reason": "test"} if action in ("cancel", "no-show") else None)
            assert res.status_code == 200, res.text
        return tenant, stay

    def review(self, tenant, stay, rating=5, comment="Clean rooms and friendly staff"):
        return self.client.post(
            REVIEWS, json={"stay_id": stay["id"], "rating": rating, "comment": comment},
            headers=tenant["headers"])

    def make(self, rating=5, comment="Nice place", state="checked_out"):
        tenant, stay = self.stay(state=state)
        res = self.review(tenant, stay, rating, comment)
        assert res.status_code == 201, res.text
        return tenant, stay, res.json()

    def get(self, path, who=None, **params):
        res = self.client.get(path, params=params, headers=(who or self.owner)["headers"])
        assert res.status_code == 200, res.text
        return res.json()

    def respond(self, review, text_="Thank you!", owner=None, prop=None):
        owner = owner or self.owner
        return self.client.put(
            f"{pp(prop or self.prop)}/reviews/{review['id']}/response",
            json={"response": text_}, headers=owner["headers"])


@pytest.fixture()
def ctx(client, world):
    return Ctx(client, world)


# ------------------------------------------------------------------ rating


@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_valid_ratings(ctx, rating):
    tenant, stay = ctx.stay()
    res = ctx.review(tenant, stay, rating)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["rating"] == rating and body["stay_id"] == stay["id"]
    assert body["property_id"] == ctx.prop["id"] and body["property_name"] == "Sunrise PG"
    assert body["owner_response"] is None and body["edited_at"] is None


@pytest.mark.parametrize("rating", [0, 6, -1, 4.5, "4", True, None, 100])
def test_invalid_ratings_rejected(ctx, rating):
    tenant, stay = ctx.stay()
    assert ctx.review(tenant, stay, rating).status_code == 422


@pytest.mark.parametrize("comment", ["", "   ", "x" * 2001])
def test_invalid_comments_rejected(ctx, comment):
    tenant, stay = ctx.stay()
    assert ctx.review(tenant, stay, 4, comment).status_code == 422


def test_unknown_fields_cannot_set_tenant_property_or_response(ctx):
    tenant, stay = ctx.stay()
    for extra in ({"tenant_id": ctx.world.tenant()["id"]}, {"property_id": ctx.prop["id"]},
                  {"owner_response": "hi"}, {"created_at": "2000-01-01T00:00:00Z"}):
        res = ctx.client.post(REVIEWS, headers=tenant["headers"], json={
            "stay_id": stay["id"], "rating": 5, "comment": "ok", **extra})
        assert res.status_code == 422, extra
    assert ctx.review(tenant, stay).status_code == 201


# ------------------------------------------------------------- eligibility


def test_checked_in_and_checked_out_stays_qualify(ctx):
    for state in ("checked_in", "checked_out"):
        tenant, stay = ctx.stay(state=state)
        assert ctx.review(tenant, stay).status_code == 201, state


@pytest.mark.parametrize("state", ["reserved", "cancelled", "no_show"])
def test_stays_without_actual_check_in_do_not_qualify(ctx, state):
    tenant, stay = ctx.stay(state=state)
    res = ctx.review(tenant, stay)
    assert res.status_code == 409 and "checked in" in res.json()["detail"]
    assert ctx.get(REVIEWS, tenant)["total"] == 0


def test_booking_request_alone_does_not_qualify(ctx):
    tenant = ctx.world.tenant()
    request = book(ctx.client, tenant, ctx.bed(), days(5)).json()
    res = ctx.client.post(REVIEWS, headers=tenant["headers"], json={
        "stay_id": request["id"], "rating": 5, "comment": "never stayed"})
    assert res.status_code == 404  # a request id is not a stay
    assert ctx.get(REVIEWS, tenant)["total"] == 0


def test_cannot_review_someone_elses_or_unknown_stay(ctx):
    owner_tenant, stay = ctx.stay()
    stranger = ctx.world.tenant()
    assert ctx.review(stranger, stay).status_code == 404
    import uuid
    assert ctx.review(stranger, {"id": str(uuid.uuid4())}).status_code == 404
    assert ctx.review(owner_tenant, stay).status_code == 201


# -------------------------------------------------------------- uniqueness


def test_one_review_per_stay_and_a_new_stay_can_be_reviewed(ctx, db_session):
    tenant, stay = ctx.stay()
    assert ctx.review(tenant, stay, 4).status_code == 201
    dup = ctx.review(tenant, stay, 5)
    assert dup.status_code == 409 and "already reviewed" in dup.json()["detail"]
    assert db_session.execute(text("select count(*) from reviews")).scalar() == 1
    # separate eligible stay for the same tenant and property
    _, second = ctx.stay(tenant=tenant)
    assert second["id"] != stay["id"]
    assert ctx.review(tenant, second, 2).status_code == 201
    assert ctx.get(REVIEWS, tenant)["total"] == 2


def test_database_enforces_one_review_per_stay_and_value_checks(ctx, db_session):
    tenant, stay = ctx.stay()
    ctx.review(tenant, stay)
    for sql in (
        "insert into reviews (id, stay_id, rating, comment) values (gen_random_uuid(), :s, 3, 'again')",
    ):
        with pytest.raises(IntegrityError):
            db_session.execute(text(sql), {"s": stay["id"]})
            db_session.commit()
        db_session.rollback()
    _, other = ctx.stay()
    for rating, comment in ((0, "x"), (6, "x"), (3, "   ")):
        with pytest.raises(IntegrityError):
            db_session.execute(text(
                "insert into reviews (id, stay_id, rating, comment) "
                "values (gen_random_uuid(), :s, :r, :c)"),
                {"s": other["id"], "r": rating, "c": comment})
            db_session.commit()
        db_session.rollback()
    with pytest.raises(IntegrityError):  # response columns must be set together
        db_session.execute(text("update reviews set owner_response='x' where stay_id=:s"),
                           {"s": stay["id"]})
        db_session.commit()
    db_session.rollback()


def test_concurrent_duplicate_submissions_create_one_review(ctx, db_session):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.db.session import SessionLocal
    from app.models.tenant import Tenant
    from app.schemas.review import ReviewCreate
    from app.services import review_service
    from app.services.errors import AppError
    import uuid

    tenant, stay = ctx.stay()
    barrier = threading.Barrier(4)

    def attempt(_):
        session = SessionLocal()
        try:
            t = session.get(Tenant, uuid.UUID(tenant["id"]))
            barrier.wait(timeout=10)
            review_service.create_review(session, t, ReviewCreate(
                stay_id=uuid.UUID(stay["id"]), rating=5, comment="race"))
            return "ok"
        except AppError as exc:
            return exc.status_code
        finally:
            session.close()

    with ThreadPoolExecutor(4) as pool:
        results = sorted(str(r) for r in pool.map(attempt, range(4)))
    assert results == ["409", "409", "409", "ok"]
    assert db_session.execute(text("select count(*) from reviews")).scalar() == 1


# ------------------------------------------------- tenant view / edit / delete


def test_tenant_views_edits_and_deletes_own_review(ctx):
    tenant, stay, review = ctx.make(3, "okay")
    assert ctx.get(f"{REVIEWS}/{review['id']}", tenant)["comment"] == "okay"
    ctx.respond(review)
    res = ctx.client.patch(f"{REVIEWS}/{review['id']}", headers=tenant["headers"],
                           json={"rating": 4})
    assert res.status_code == 200
    body = res.json()
    assert body["rating"] == 4 and body["comment"] == "okay" and body["edited_at"] is not None
    assert body["owner_response"] == "Thank you!"  # response survives an edit
    res = ctx.client.patch(f"{REVIEWS}/{review['id']}", headers=tenant["headers"],
                           json={"comment": "much better after fixes"})
    assert res.json()["comment"] == "much better after fixes" and res.json()["rating"] == 4
    for bad in ({}, {"rating": 9}, {"comment": " "}, {"stay_id": stay["id"]}, {"owner_response": "x"}):
        assert ctx.client.patch(f"{REVIEWS}/{review['id']}", headers=tenant["headers"],
                                json=bad).status_code == 422, bad
    assert ctx.client.delete(f"{REVIEWS}/{review['id']}", headers=tenant["headers"]).status_code == 204
    assert ctx.client.get(f"{REVIEWS}/{review['id']}", headers=tenant["headers"]).status_code == 404
    assert ctx.client.delete(f"{REVIEWS}/{review['id']}", headers=tenant["headers"]).status_code == 404
    # deleting frees the stay to be reviewed again (still one live review per stay)
    assert ctx.review(tenant, stay, 5).status_code == 201


def test_unchanged_edit_does_not_mark_edited(ctx):
    tenant, _, review = ctx.make(3, "same")
    res = ctx.client.patch(f"{REVIEWS}/{review['id']}", headers=tenant["headers"],
                           json={"rating": 3, "comment": "same"})
    assert res.status_code == 200 and res.json()["edited_at"] is None


def test_other_tenant_cannot_see_edit_or_delete(ctx):
    owner_tenant, _, review = ctx.make()
    other = ctx.world.tenant()
    url = f"{REVIEWS}/{review['id']}"
    assert ctx.client.get(url, headers=other["headers"]).status_code == 404
    assert ctx.client.patch(url, headers=other["headers"], json={"rating": 1}).status_code == 404
    assert ctx.client.delete(url, headers=other["headers"]).status_code == 404
    assert ctx.get(REVIEWS, other)["total"] == 0
    assert ctx.get(url, owner_tenant)["rating"] == 5  # untouched


def test_authentication_and_role_separation(ctx):
    tenant, _, review = ctx.make()
    for method, url in (("get", REVIEWS), ("post", REVIEWS),
                        ("get", f"{REVIEWS}/{review['id']}"),
                        ("get", "/api/v1/reviews"),
                        ("get", f"{pp(ctx.prop)}/reviews"),
                        ("get", f"{pp(ctx.prop)}/review-summary"),
                        ("put", f"{pp(ctx.prop)}/reviews/{review['id']}/response")):
        assert getattr(ctx.client, method)(url).status_code == 401, url
    # role tokens are not interchangeable
    assert ctx.client.get(REVIEWS, headers=ctx.owner["headers"]).status_code == 401
    for url in ("/api/v1/reviews", f"{pp(ctx.prop)}/reviews", f"{pp(ctx.prop)}/review-summary"):
        assert ctx.client.get(url, headers=tenant["headers"]).status_code == 401, url
    assert ctx.client.put(f"{pp(ctx.prop)}/reviews/{review['id']}/response",
                          json={"response": "x"}, headers=tenant["headers"]).status_code == 401


# -------------------------------------------------------------------- owner


def test_owner_views_reviews_with_tenant_and_stay_info(ctx):
    tenant, stay, review = ctx.make(4, "Good")
    (item,) = ctx.get(f"{pp(ctx.prop)}/reviews")["items"]
    assert item["id"] == review["id"] and item["tenant_id"] == tenant["id"]
    assert item["tenant_full_name"] == "Tina Tenant"
    assert item["stay_check_in"] == days(0) and item["stay_check_out"] == days(0)
    assert ctx.get(f"{pp(ctx.prop)}/reviews/{review['id']}")["rating"] == 4
    assert ctx.get("/api/v1/reviews")["total"] == 1


def test_owner_responds_but_cannot_change_rating_or_text(ctx):
    tenant, _, review = ctx.make(2, "Noisy at night")
    res = ctx.respond(review, "  We are sorry, fixed the AC.  ")
    assert res.status_code == 200
    body = res.json()
    assert body["owner_response"] == "We are sorry, fixed the AC."
    assert body["owner_responded_at"] is not None
    assert body["rating"] == 2 and body["comment"] == "Noisy at night"
    url = f"{pp(ctx.prop)}/reviews/{review['id']}/response"
    for bad in ({"response": "x", "rating": 5}, {"response": "x", "comment": "great"},
                {"rating": 5}, {"comment": "great"}, {"response": ""}, {"response": "  "},
                {"response": "x" * 2001}):
        assert ctx.client.put(url, json=bad, headers=ctx.owner["headers"]).status_code == 422, bad
    # no endpoint lets the owner edit or delete the review itself
    base = f"{pp(ctx.prop)}/reviews/{review['id']}"
    for method in ("patch", "put", "delete", "post"):
        res = ctx.client.request(method.upper(), base, json={"rating": 5},
                                 headers=ctx.owner["headers"])
        assert res.status_code == 405, method
    again = ctx.get(f"{REVIEWS}/{review['id']}", tenant)
    assert again["rating"] == 2 and again["comment"] == "Noisy at night"
    assert ctx.respond(review, "Updated reply").json()["owner_response"] == "Updated reply"
    removed = ctx.client.delete(url, headers=ctx.owner["headers"])
    assert removed.status_code == 200 and removed.json()["owner_response"] is None
    assert removed.json()["rating"] == 2
    assert ctx.client.delete(url, headers=ctx.owner["headers"]).status_code == 404


def test_cross_owner_access_is_denied(client, world):
    a, b = Ctx(client, world), Ctx(client, world)
    tenant, _, review = a.make(5, "A's review")
    for url in (f"{pp(a.prop)}/reviews", f"{pp(a.prop)}/review-summary",
                f"{pp(a.prop)}/reviews/{review['id']}"):
        assert client.get(url, headers=b.owner["headers"]).status_code == 404, url
    # their own property id + a review id from another owner's property
    assert client.get(f"{pp(b.prop)}/reviews/{review['id']}",
                      headers=b.owner["headers"]).status_code == 404
    assert client.get(f"{pp(a.prop)}/reviews/{review['id']}",
                      headers=a.owner["headers"]).status_code == 200
    assert a.respond(review, owner=b.owner).status_code == 404
    assert b.respond(review, prop=b.prop).status_code == 404  # right owner, wrong property
    assert client.delete(f"{pp(a.prop)}/reviews/{review['id']}/response",
                         headers=b.owner["headers"]).status_code == 404
    assert client.get("/api/v1/reviews", params={"property_id": a.prop["id"]},
                      headers=b.owner["headers"]).status_code == 404
    assert b.get("/api/v1/reviews")["total"] == 0 and b.get("/api/v1/reviews")["items"] == []
    assert a.get(f"{REVIEWS}/{review['id']}", tenant)["owner_response"] is None


def test_owner_cannot_use_tenant_endpoints_and_responses_are_per_review(ctx):
    _, _, first = ctx.make()
    _, _, second = ctx.make()
    ctx.respond(first, "only first")
    items = {i["id"]: i for i in ctx.get("/api/v1/reviews")["items"]}
    assert items[first["id"]]["owner_response"] == "only first"
    assert items[second["id"]]["owner_response"] is None


# --------------------------------------------------- summaries and listings


def test_rating_summary_average_and_distribution(ctx):
    empty = ctx.get(f"{pp(ctx.prop)}/review-summary")
    assert empty == {"property_id": ctx.prop["id"], "total_reviews": 0, "average_rating": None,
                     "rating_counts": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
                     "responded_reviews": 0}
    made = [ctx.make(r)[2] for r in (5, 5, 4, 3, 1, 5)]
    ctx.respond(made[0])
    summary = ctx.get(f"{pp(ctx.prop)}/review-summary")
    assert summary["total_reviews"] == 6
    assert summary["average_rating"] == "3.83"  # 23 / 6 = 3.8333
    assert summary["rating_counts"] == {"1": 1, "2": 0, "3": 1, "4": 1, "5": 3}
    assert sum(summary["rating_counts"].values()) == summary["total_reviews"]
    assert summary["responded_reviews"] == 1


def test_summary_updates_after_edit_and_delete(ctx):
    tenant, _, review = ctx.make(1)
    ctx.make(5)
    assert ctx.get(f"{pp(ctx.prop)}/review-summary")["average_rating"] == "3.00"
    ctx.client.patch(f"{REVIEWS}/{review['id']}", headers=tenant["headers"], json={"rating": 3})
    assert ctx.get(f"{pp(ctx.prop)}/review-summary")["average_rating"] == "4.00"
    ctx.client.delete(f"{REVIEWS}/{review['id']}", headers=tenant["headers"])
    summary = ctx.get(f"{pp(ctx.prop)}/review-summary")
    assert summary["total_reviews"] == 1 and summary["average_rating"] == "5.00"
    assert summary["rating_counts"]["3"] == 0


def test_average_rounds_half_up(ctx):
    for r in (5, 4, 4, 4, 4, 4, 4, 4):  # 33 / 8 = 4.125 -> 4.13
        ctx.make(r)
    assert ctx.get(f"{pp(ctx.prop)}/review-summary")["average_rating"] == "4.13"


def test_summaries_and_listings_are_per_property(client, world):
    ctx = Ctx(client, world)
    other = world.property(ctx.owner, rooms=1, beds_per_room=2)
    ctx.make(5)
    ctx.make(4)
    tenant, stay = ctx.stay(prop=other)
    assert ctx.review(tenant, stay, 1).status_code == 201
    mine = ctx.get(f"{pp(ctx.prop)}/review-summary")
    theirs = ctx.get(f"{pp(other)}/review-summary")
    assert (mine["total_reviews"], mine["average_rating"]) == (2, "4.50")
    assert (theirs["total_reviews"], theirs["average_rating"]) == (1, "1.00")
    everything = ctx.get("/api/v1/reviews")
    assert everything["total"] == 3
    only = ctx.get("/api/v1/reviews", property_id=other["id"])
    assert [i["property_id"] for i in only["items"]] == [other["id"]]
    assert ctx.get(f"{pp(ctx.prop)}/reviews")["total"] == 2
    # tenant sees reviews across properties and can filter by property
    assert ctx.get(REVIEWS, tenant)["total"] == 1
    assert ctx.get(REVIEWS, tenant, property_id=ctx.prop["id"])["total"] == 0


def test_pagination_order_and_filters(ctx):
    made = [ctx.make(r)[2] for r in (1, 2, 3, 4, 5)]
    ctx.respond(made[4])
    full = [i["id"] for i in ctx.get(f"{pp(ctx.prop)}/reviews")["items"]]
    assert full == [m["id"] for m in reversed(made)]  # newest first
    seen = []
    for offset in (0, 2, 4):
        page = ctx.get(f"{pp(ctx.prop)}/reviews", limit=2, offset=offset)
        assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == offset
        seen += [i["id"] for i in page["items"]]
    assert seen == full
    assert ctx.get(f"{pp(ctx.prop)}/reviews", limit=2, offset=10)["items"] == []
    assert [i["rating"] for i in ctx.get(f"{pp(ctx.prop)}/reviews", rating=3)["items"]] == [3]
    assert ctx.get(f"{pp(ctx.prop)}/reviews", has_response="true")["total"] == 1
    assert ctx.get(f"{pp(ctx.prop)}/reviews", has_response="false")["total"] == 4
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"rating": 0}, {"rating": 6}):
        res = ctx.client.get(f"{pp(ctx.prop)}/reviews", params=bad, headers=ctx.owner["headers"])
        assert res.status_code == 422, bad
    tenant, _, mine = ctx.make()
    page = ctx.get(REVIEWS, tenant, limit=1)
    assert page["total"] == 1 and [i["id"] for i in page["items"]] == [mine["id"]]


def test_owner_can_still_read_reviews_after_property_deletion(ctx):
    _, _, review = ctx.make(4)
    res = ctx.client.delete(pp(ctx.prop), headers=ctx.owner["headers"])
    assert res.status_code in (200, 204), res.text
    assert ctx.get(f"{pp(ctx.prop)}/review-summary")["total_reviews"] == 1
    assert ctx.get(f"{pp(ctx.prop)}/reviews")["total"] == 1
    # but responding is a mutation and stays closed for a deleted property
    assert ctx.respond(review).status_code == 404


def test_deleted_review_removes_response_and_sibling_reviews_untouched(ctx, db_session):
    tenant, _, review = ctx.make()
    _, _, other = ctx.make()
    ctx.respond(review)
    assert ctx.client.delete(f"{REVIEWS}/{review['id']}", headers=tenant["headers"]).status_code == 204
    assert db_session.execute(text("select count(*) from reviews")).scalar() == 1
    assert db_session.execute(text("select id::text from reviews")).scalar() == other["id"]
