"""Module 8: rent tracking dashboard, owner overview and reports."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services import stay_rules
from tests.conftest import days
from tests.flows import TP, pp, reserve, stay_action

RENT = "/api/v1/dashboard/rent-due"
D = Decimal


class Ctx:
    """One owner/property with helpers to create tenants and obligations."""

    def __init__(self, client, world, beds=6, rent="5000.00"):
        self.client, self.world = client, world
        self.owner = world.owner()
        self.prop = world.property(self.owner, rooms=1, beds_per_room=beds, rent=rent)
        self._next_bed = 0

    def stay(self, tenant=None, check_in=0):
        tenant = tenant or self.world.tenant()
        bed = self.prop["beds"][self._next_bed]["id"]
        self._next_bed += 1
        stay = reserve(self.client, self.owner, self.prop, tenant, bed, days(check_in))
        return tenant, stay

    def obligation(self, stay, due, period=0, amount=None):
        body = {"period_start": days(period), "due_date": days(due)}
        if amount:
            body["amount"] = amount
        res = self.client.post(
            f"{pp(self.prop)}/stays/{stay['id']}/rent-obligations",
            json=body, headers=self.owner["headers"],
        )
        assert res.status_code == 201, res.text
        return res.json()

    def pay(self, ob, amount):
        res = self.client.post(
            f"{pp(self.prop)}/rent-obligations/{ob['id']}/payments",
            json={"amount": amount, "method": "cash", "paid_on": days(0)},
            headers=self.owner["headers"],
        )
        assert res.status_code == 201, res.text
        return res.json()

    def reverse(self, ob, payment):
        res = self.client.post(
            f"{pp(self.prop)}/rent-obligations/{ob['id']}/payments/{payment['id']}/reverse",
            json={"reason": "entered in error"}, headers=self.owner["headers"],
        )
        assert res.status_code == 200, res.text

    def waive(self, ob, amount):
        res = self.client.post(
            f"{pp(self.prop)}/rent-obligations/{ob['id']}/adjustments",
            json={"amount": amount, "reason": "goodwill"}, headers=self.owner["headers"],
        )
        assert res.status_code == 201, res.text

    def claim(self, tenant, ob, amount="1000.00"):
        res = self.client.post(
            f"{TP}/rent-obligations/{ob['id']}/payment-claims",
            json={"amount": amount, "method": "upi", "paid_on": days(0)},
            headers=tenant["headers"],
        )
        assert res.status_code == 201, res.text
        return res.json()

    def get(self, path=RENT, **params):
        res = self.client.get(path, params=params, headers=self.owner["headers"])
        assert res.status_code == 200, res.text
        return res.json()


@pytest.fixture()
def ctx(client, world):
    return Ctx(client, world)


def ids(rows):
    return [r["obligation_id"] for r in rows]


# ------------------------------------------------------------------ windows


def test_default_window_is_seven_days_inclusive_of_today_and_day_seven(ctx):
    _, stay = ctx.stay()
    obs = {off: ctx.obligation(stay, off, period=-(off + 40)) for off in (-1, 0, 7, 8)}
    body = ctx.get()
    assert body["filters"]["within_days"] == 7
    assert body["filters"]["window_start"] == days(0)
    assert body["filters"]["window_end"] == days(7)
    assert set(ids(body["upcoming"]["items"])) == {obs[0]["id"], obs[7]["id"]}
    assert ids(body["upcoming"]["items"]) == [obs[0]["id"], obs[7]["id"]]  # nearest first
    assert ids(body["overdue"]["items"]) == [obs[-1]["id"]]


@pytest.mark.parametrize("n", [3, 5, 7, 15, 30])
def test_configurable_windows_include_exact_last_day(ctx, n):
    _, stay = ctx.stay()
    inside = ctx.obligation(stay, n, period=-100)
    outside = ctx.obligation(stay, n + 1, period=-200)
    body = ctx.get(within_days=n)
    assert ids(body["upcoming"]["items"]) == [inside["id"]]
    assert outside["id"] not in ids(body["upcoming"]["items"])
    assert body["upcoming"]["items"][0]["days_until_due"] == n
    assert body["overview"]["due_soon"] == {"tenant_count": 1, "obligation_count": 1}


def test_window_validation(ctx):
    for bad in ({"within_days": 0}, {"within_days": 366}, {"within_days": "x"},
                {"status": "overdue"}, {"page": 0}, {"page_size": 101},
                {"overdue_page_size": 0}):
        res = ctx.client.get(RENT, params=bad, headers=ctx.owner["headers"])
        assert res.status_code == 422, bad
    assert ctx.get(within_days=365)["filters"]["within_days"] == 365


def test_overdue_is_independent_of_window(ctx):
    _, stay = ctx.stay()
    old = ctx.obligation(stay, -60, period=-60)
    body = ctx.get(within_days=1)
    assert body["upcoming"]["items"] == []
    (row,) = body["overdue"]["items"]
    assert row["obligation_id"] == old["id"]
    assert row["days_overdue"] == 60 and row["days_until_due"] is None
    assert row["timing"] == "overdue"
    assert body["overview"]["overdue"] == {"tenant_count": 1, "obligation_count": 1}
    assert body["overview"]["total_overdue_amount"] == "5000.00"


# --------------------------------------------------- statuses and verified paid


def test_statuses_pending_and_rejected_claims_and_filters(ctx):
    ta, sa = ctx.stay()
    tb, sb = ctx.stay()
    tc, sc = ctx.stay()
    td, sd = ctx.stay()
    unpaid = ctx.obligation(sa, 2)
    partial = ctx.obligation(sb, 2)
    paid = ctx.obligation(sc, 2)
    claimed = ctx.obligation(sd, 2)
    ctx.pay(partial, "1500.00")
    ctx.pay(paid, "5000.00")
    pending = ctx.claim(td, claimed, "5000.00")  # pending: must not count
    # a rejected claim on the unpaid one changes nothing either
    c = ctx.claim(ta, unpaid, "2000.00")
    ctx.client.post(
        f"{pp(ctx.prop)}/rent-obligations/{unpaid['id']}/payment-claims/{c['id']}/reject",
        json={}, headers=ctx.owner["headers"],
    )
    body = ctx.get()
    rows = {r["obligation_id"]: r for r in body["upcoming"]["items"]}
    assert rows[unpaid["id"]]["payment_status"] == "unpaid"
    assert rows[claimed["id"]]["payment_status"] == "unpaid"
    assert rows[claimed["id"]]["has_pending_claim"] is True
    assert rows[unpaid["id"]]["has_pending_claim"] is False
    assert rows[partial["id"]]["payment_status"] == "partially_paid"
    assert rows[partial["id"]]["amount_paid"] == "1500.00"
    assert rows[partial["id"]]["outstanding"] == "3500.00"
    assert rows[paid["id"]]["payment_status"] == "paid"
    ov = body["overview"]
    assert ov["unpaid"]["obligation_count"] == 2
    assert ov["partially_paid"]["obligation_count"] == 1
    assert ov["paid"]["obligation_count"] == 1
    assert ov["due_soon"]["obligation_count"] == 3  # paid is not "due soon"
    assert ov["total_collected"] == "6500.00"
    assert ov["total_outstanding"] == "13500.00"
    assert body["pending_claims"]["claim_count"] == 1
    assert body["pending_claims"]["total_claimed_amount"] == "5000.00"
    assert pending["status"] == "pending"

    for status, expected in (("unpaid", {unpaid["id"], claimed["id"]}),
                             ("partially_paid", {partial["id"]}),
                             ("paid", {paid["id"]})):
        b = ctx.get(status=status)
        assert set(ids(b["upcoming"]["items"])) == expected
        assert b["upcoming"]["summary"]["obligation_count"] == len(expected)
        assert b["upcoming"]["pagination"]["total_items"] == len(expected)
        assert b["overview"]["total_collected"] == "6500.00"  # cards unaffected


def test_status_filter_applies_to_overdue_list(ctx):
    ta, sa = ctx.stay()
    tb, sb = ctx.stay()
    unpaid = ctx.obligation(sa, -5, period=-5)
    partial = ctx.obligation(sb, -5, period=-5)
    ctx.pay(partial, "100.00")
    assert ids(ctx.get(status="unpaid")["overdue"]["items"]) == [unpaid["id"]]
    assert ids(ctx.get(status="partially_paid")["overdue"]["items"]) == [partial["id"]]
    assert ctx.get(status="paid")["overdue"]["items"] == []
    # overdue is a date class, not a replacement for payment status
    rows = ctx.get()["overdue"]["items"]
    assert {r["payment_status"] for r in rows} == {"unpaid", "partially_paid"}


def test_reversal_and_adjustment_change_balances(ctx):
    _, stay = ctx.stay()
    ob = ctx.obligation(stay, 1)
    p = ctx.pay(ob, "5000.00")
    assert ctx.get()["upcoming"]["items"][0]["payment_status"] == "paid"
    ctx.reverse(ob, p)
    row = ctx.get()["upcoming"]["items"][0]
    assert row["payment_status"] == "unpaid" and row["amount_paid"] == "0.00"
    assert ctx.get()["overview"]["total_collected"] == "0.00"
    ctx.pay(ob, "1000.00")
    ctx.waive(ob, "1500.00")
    row = ctx.get()["upcoming"]["items"][0]
    assert row["amount_due"] == "5000.00" and row["amount_adjusted"] == "1500.00"
    assert row["effective_amount_due"] == "3500.00"
    assert row["outstanding"] == "2500.00" and row["payment_status"] == "partially_paid"
    ctx.waive(ob, "2500.00")  # fully settled by audited waiver -> no balance left
    row = ctx.get()["upcoming"]["items"][0]
    assert row["outstanding"] == "0.00" and row["payment_status"] == "paid"
    body = ctx.get()
    assert body["overview"]["total_outstanding"] == "0.00"
    assert body["overview"]["total_collected"] == "1000.00"


# ------------------------------------------------- distinct tenants / no inflation


def test_tenant_count_differs_from_obligation_count_and_totals_not_inflated(ctx):
    ta, sa = ctx.stay()
    tb, sb = ctx.stay()
    a1 = ctx.obligation(sa, 1, period=0)
    a2 = ctx.obligation(sa, 3, period=-30)
    a3 = ctx.obligation(sa, -2, period=-60)
    b1 = ctx.obligation(sb, 2)
    # many payments and claims on one obligation must not multiply anything
    for _ in range(3):
        ctx.pay(a1, "100.00")
    ctx.claim(ta, a2, "100.00")
    ctx.client.post(f"{TP}/rent-obligations/{b1['id']}/payment-claims",
                    json={"amount": "50.00", "method": "cash", "paid_on": days(0)},
                    headers=tb["headers"])
    body = ctx.get()
    up = body["upcoming"]
    assert len(up["items"]) == 3 and len({*ids(up["items"])}) == 3
    assert up["summary"]["obligation_count"] == 3
    assert up["summary"]["tenant_count"] == 2
    assert up["summary"]["total_paid"] == "300.00"
    assert up["summary"]["total_outstanding"] == "14700.00"
    assert body["overview"]["due_soon"] == {"tenant_count": 2, "obligation_count": 3}
    assert body["overdue"]["summary"]["tenant_count"] == 1
    assert body["overdue"]["summary"]["obligation_count"] == 1
    assert body["overview"]["total_outstanding"] == "19700.00"
    assert body["pending_claims"]["claim_count"] == 2
    assert body["pending_claims"]["tenant_count"] == 2
    assert a3["id"] in ids(body["overdue"]["items"])


def test_row_details_and_bed_info(ctx):
    t, stay = ctx.stay()
    ob = ctx.obligation(stay, 4)
    row = ctx.get()["upcoming"]["items"][0]
    assert row["tenant_id"] == t["id"] and row["tenant_email"] == t["email"]
    assert row["property_name"] == "Sunrise PG" and row["room_number"] == "101"
    assert row["bed_label"] == "B1" and row["days_until_due"] == 4
    assert row["period_start"] == days(0) and row["timing"] == "upcoming"
    assert row["stay_id"] == stay["id"] and row["obligation_id"] == ob["id"]


# ---------------------------------------------- filters, pagination, ordering


def test_property_filter_search_and_pagination_ordering(client, world):
    ctx = Ctx(client, world)
    other = world.property(ctx.owner, rooms=1, beds_per_room=1)
    tenants = [ctx.stay()[1] for _ in range(4)]
    obs = [ctx.obligation(s, 2) for s in tenants]  # identical due dates: id tie-break
    t2 = world.tenant()
    s2 = reserve(client, ctx.owner, other, t2, other["beds"][0]["id"], days(0))
    res = client.post(f"{pp(other)}/stays/{s2['id']}/rent-obligations",
                      json={"period_start": days(0), "due_date": days(1)},
                      headers=ctx.owner["headers"])
    other_ob = res.json()
    everything = ctx.get()
    assert everything["upcoming"]["items"][0]["obligation_id"] == other_ob["id"]
    scoped = ctx.get(property_id=other["id"])
    assert ids(scoped["upcoming"]["items"]) == [other_ob["id"]]
    assert scoped["overview"]["total_outstanding"] == "5000.00"
    assert scoped["filters"]["property_id"] == other["id"]

    full = ids(ctx.get(property_id=ctx.prop["id"])["upcoming"]["items"])
    assert len(full) == 4
    seen = []
    for page in (1, 2, 3):
        b = ctx.get(property_id=ctx.prop["id"], page=page, page_size=2)
        assert b["upcoming"]["pagination"] == {
            "page": page, "page_size": 2, "total_items": 4, "total_pages": 2}
        seen += ids(b["upcoming"]["items"])
    assert seen == full and len(set(seen)) == 4
    assert full == ids(ctx.get(property_id=ctx.prop["id"])["upcoming"]["items"])  # stable
    assert ctx.get(search="zzz-nobody")["upcoming"]["summary"]["obligation_count"] == 0
    assert ctx.get(search="Tina")["upcoming"]["summary"]["obligation_count"] >= 1
    assert ctx.get(search="%")["upcoming"]["items"] == []  # wildcard is escaped
    assert obs  # created


def test_overdue_ordering_oldest_first_and_independent_paging(ctx):
    _, stay = ctx.stay()
    a = ctx.obligation(stay, -3, period=-100)
    b = ctx.obligation(stay, -30, period=-200)
    c = ctx.obligation(stay, -10, period=-300)
    body = ctx.get(overdue_page_size=2)
    assert ids(body["overdue"]["items"]) == [b["id"], c["id"]]
    assert body["overdue"]["pagination"]["total_pages"] == 2
    body = ctx.get(overdue_page=2, overdue_page_size=2)
    assert ids(body["overdue"]["items"]) == [a["id"]]
    assert body["upcoming"]["pagination"]["page"] == 1


def test_unknown_or_foreign_property_is_404(client, world, ctx):
    stranger = Ctx(client, world)
    for path in (RENT, "/api/v1/dashboard/overview", "/api/v1/reports/monthly-collections",
                 "/api/v1/reports/collection-trends", "/api/v1/reports/rent-obligations"):
        res = client.get(path, params={"property_id": ctx.prop["id"]},
                         headers=stranger.owner["headers"])
        assert res.status_code == 404, path


# ----------------------------------------------------------------- timezone


class _FrozenDatetime(datetime):
    frozen = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)  # 01:30 IST on the 13th

    @classmethod
    def now(cls, tz=None):
        return cls.frozen.astimezone(tz) if tz else cls.frozen


@pytest.mark.parametrize("zone,expected", [("Asia/Kolkata", date(2026, 10, 13)),
                                           ("America/Los_Angeles", date(2026, 10, 12))])
def test_today_uses_business_timezone(ctx, monkeypatch, zone, expected):
    from app.core.config import get_settings

    monkeypatch.setattr(stay_rules, "datetime", _FrozenDatetime)
    monkeypatch.setattr(get_settings(), "business_timezone", zone)
    body = ctx.get(within_days=1)
    assert body["filters"]["today"] == expected.isoformat()
    assert body["filters"]["timezone"] == zone
    assert body["filters"]["window_end"] == date.fromordinal(expected.toordinal() + 1).isoformat()


def test_boundary_dates_in_business_timezone(ctx, monkeypatch):
    _, stay = ctx.stay()
    # Insert directly: API refuses nothing here, but avoids today-relative helpers.
    from app.db.session import SessionLocal
    from app.models.rent import RentObligation
    import uuid

    db = SessionLocal()
    try:
        for i, due in enumerate((date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14))):
            db.add(RentObligation(
                id=uuid.uuid4(), stay_id=stay["id"], tenant_id=stay["tenant_id"],
                property_id=ctx.prop["id"], period_start=date(2026, 1, 1 + i),
                period_end=date(2026, 2, 1 + i), due_date=due, amount_due=D("100.00")))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(stay_rules, "datetime", _FrozenDatetime)
    body = ctx.get(within_days=1)  # IST: today = 13th
    assert [r["due_date"] for r in body["upcoming"]["items"]] == ["2026-10-13", "2026-10-14"]
    assert [r["due_date"] for r in body["overdue"]["items"]] == ["2026-10-12"]


# ----------------------------------------------------------------- security


def test_owner_isolation_and_authentication(client, world):
    a, b = Ctx(client, world), Ctx(client, world)
    ta, sa = a.stay()
    ob = a.obligation(sa, 2)
    a.pay(ob, "500.00")
    a.claim(ta, ob, "100.00")
    for path, params in ((RENT, {}), ("/api/v1/dashboard/overview", {}),
                         ("/api/v1/reports/rent-obligations", {}),
                         ("/api/v1/reports/property-rent-summary", {}),
                         ("/api/v1/reports/monthly-collections", {}),
                         ("/api/v1/reports/collection-trends", {})):
        assert client.get(path, params=params).status_code == 401
        assert client.get(path, params=params, headers=ta["headers"]).status_code == 401
    mine, theirs = a.get(), b.get()
    assert mine["upcoming"]["summary"]["obligation_count"] == 1
    assert theirs["upcoming"]["items"] == [] and theirs["overdue"]["items"] == []
    assert theirs["overview"]["total_collected"] == "0.00"
    assert theirs["pending_claims"]["claim_count"] == 0
    assert b.get("/api/v1/reports/rent-obligations")["items"] == []
    ov = b.get("/api/v1/dashboard/overview")
    assert ov["collected_in_range"] == "0.00" and ov["pending_claims"]["claim_count"] == 0
    assert [p["property_id"] for p in b.get("/api/v1/reports/property-rent-summary")["items"]] \
        == [b.prop["id"]]
    assert b.get("/api/v1/reports/monthly-collections")["total_collected"] == "0.00"


def test_endpoints_are_read_only(ctx, db_session):
    _, stay = ctx.stay()
    ctx.obligation(stay, 1)
    before = db_session.execute(text(
        "select (select count(*) from rent_payments), (select count(*) from notifications), "
        "(select sum(amount_paid) from rent_obligations)")).one()
    ctx.get(); ctx.get("/api/v1/dashboard/overview"); ctx.get("/api/v1/reports/rent-obligations")
    db_session.rollback()
    assert before == db_session.execute(text(
        "select (select count(*) from rent_payments), (select count(*) from notifications), "
        "(select sum(amount_paid) from rent_obligations)")).one()
    for method in ("post", "put", "delete", "patch"):
        assert getattr(ctx.client, method)(RENT, headers=ctx.owner["headers"]).status_code == 405


# ------------------------------------------------------------ overview/reports


def test_overview_metrics_and_occupancy(client, world):
    ctx = Ctx(client, world, beds=4)
    t1, s1 = ctx.stay()
    t2, s2 = ctx.stay()
    assert stay_action(client, ctx.owner, ctx.prop, s1["id"], "check-in").status_code == 200
    ob = ctx.obligation(s1, -2, period=-2)
    ctx.pay(ob, "2000.00")
    ov = ctx.get("/api/v1/dashboard/overview")
    assert (ov["properties"], ov["rooms"]) == (1, 1)
    assert ov["occupancy"] == {
        "total_beds": 4, "occupied_beds": 1, "reserved_beds": 1, "vacant_beds": 2,
        "unavailable_beds": 0, "occupancy_percentage": "25.00"}
    assert ov["collected_in_range"] == "2000.00"  # paid today, default range = this month
    assert ov["total_outstanding"] == "3000.00" and ov["total_overdue"] == "3000.00"
    far = ctx.get("/api/v1/dashboard/overview", from_date="2000-01-01", to_date="2000-01-31")
    assert far["collected_in_range"] == "0.00"
    bad = client.get("/api/v1/dashboard/overview",
                     params={"from_date": "2026-02-01", "to_date": "2026-01-01"},
                     headers=ctx.owner["headers"])
    assert bad.status_code == 422


def test_collection_reports_reconcile_with_ledger_and_reversals(ctx, db_session):
    ta, sa = ctx.stay()
    tb, sb = ctx.stay()
    a = ctx.obligation(sa, 1, period=-1)
    b = ctx.obligation(sb, 1, period=-1)
    ctx.pay(a, "3000.00")
    reversed_ = ctx.pay(b, "1000.00")
    ctx.pay(b, "500.00")
    ctx.reverse(b, reversed_)
    ctx.claim(ta, a, "100.00")  # pending: not collected
    # move one payment into a previous month directly to exercise grouping
    db_session.execute(text(
        "update rent_payments set paid_on = paid_on - interval '40 days' "
        "where amount = 500.00"))
    db_session.commit()

    ledger = db_session.execute(text(
        "select coalesce(sum(p.amount),0) from rent_payments p "
        "left join rent_payment_reversals r on r.payment_id=p.id where r.id is null")).scalar()
    held = db_session.execute(text("select sum(amount_paid) from rent_obligations")).scalar()
    assert ledger == held == D("3500.00")

    monthly = ctx.get("/api/v1/reports/monthly-collections")
    assert D(monthly["total_collected"]) == ledger
    assert sum(D(m["total_collected"]) for m in monthly["months"]) == ledger
    assert sum(m["payment_count"] for m in monthly["months"]) == 2
    assert [m["month"] for m in monthly["months"]] == sorted(m["month"] for m in monthly["months"])

    trends = ctx.get("/api/v1/reports/collection-trends")
    assert len(trends["points"]) == 12
    assert D(trends["total_collected"]) == ledger
    assert sum(D(p["total_collected"]) for p in trends["points"]) == ledger
    assert trends["points"][0]["change_from_previous"] is None
    for prev, cur in zip(trends["points"], trends["points"][1:]):
        assert D(cur["change_from_previous"]) == D(cur["total_collected"]) - D(prev["total_collected"])

    overview = ctx.get(RENT)["overview"]
    assert D(overview["total_collected"]) == ledger

    summary = ctx.get("/api/v1/reports/property-rent-summary")
    (p,) = summary["items"]
    assert p["property_id"] == ctx.prop["id"]
    assert D(p["collected"]) == ledger and p["obligation_count"] == 2 and p["tenant_count"] == 2
    assert D(p["outstanding"]) == D(overview["total_outstanding"]) == D("6500.00")
    assert D(p["total_effective_due"]) == D(p["collected"]) + D(p["outstanding"])
    assert p["overdue"] == "0.00"


def test_property_summary_overdue_and_obligation_report(client, world):
    ctx = Ctx(client, world)
    other = world.property(ctx.owner, rooms=1, beds_per_room=1)
    _, s1 = ctx.stay()
    od = ctx.obligation(s1, -3, period=-3)
    up = ctx.obligation(s1, 5, period=-40)
    done = ctx.obligation(s1, -90, period=-90)
    ctx.pay(done, "5000.00")
    ctx.pay(od, "1000.00")
    items = {i["property_id"]: i for i in
             ctx.get("/api/v1/reports/property-rent-summary")["items"]}
    mine = items[ctx.prop["id"]]
    assert mine["overdue"] == "4000.00" and mine["overdue_obligation_count"] == 1
    assert mine["outstanding"] == "9000.00" and mine["collected"] == "6000.00"
    assert items[other["id"]]["obligation_count"] == 0
    assert items[other["id"]]["outstanding"] == "0.00"

    report = ctx.get("/api/v1/reports/rent-obligations", page_size=2)
    assert report["summary"]["obligation_count"] == 3
    assert report["pagination"]["total_pages"] == 2
    assert [r["obligation_id"] for r in report["items"]] == [up["id"], od["id"]]  # due desc
    by = {r["obligation_id"]: r for r in ctx.get(
        "/api/v1/reports/rent-obligations")["items"]}
    assert by[done["id"]]["timing"] == "settled" and by[od["id"]]["timing"] == "overdue"
    assert by[up["id"]]["timing"] == "upcoming"
    partial = ctx.get("/api/v1/reports/rent-obligations", status="partially_paid")
    assert ids_of(partial["items"]) == [od["id"]]
    assert partial["summary"]["total_outstanding"] == "4000.00"
    window = ctx.get("/api/v1/reports/rent-obligations", due_from=days(0), due_to=days(10))
    assert ids_of(window["items"]) == [up["id"]]
    assert ctx.client.get("/api/v1/reports/rent-obligations",
                          params={"due_from": days(5), "due_to": days(1)},
                          headers=ctx.owner["headers"]).status_code == 422


def ids_of(rows):
    return [r["obligation_id"] for r in rows]


# ------------------------------------------------------------- audit additions


def test_overview_cards_ignore_status_filter(ctx):
    ta, sa = ctx.stay()
    tb, sb = ctx.stay()
    tc, sc = ctx.stay()
    ctx.obligation(sa, 2)
    part = ctx.obligation(sb, 3)
    done = ctx.obligation(sc, 4)
    ctx.obligation(sa, -4, period=-50)
    ctx.pay(part, "700.00")
    ctx.pay(done, "5000.00")
    ctx.claim(ta, ctx.obligation(sa, 6, period=-90), "100.00")
    baseline = ctx.get()
    for status in ("unpaid", "partially_paid", "paid"):
        filtered = ctx.get(status=status)
        assert filtered["overview"] == baseline["overview"], status
        assert filtered["pending_claims"] == baseline["pending_claims"], status
    # while the lists do change with the filter
    assert ctx.get(status="paid")["upcoming"]["summary"]["obligation_count"] == 1
    assert baseline["upcoming"]["summary"]["obligation_count"] == 4


def test_fully_waived_is_paid_but_never_cash(ctx):
    _, stay = ctx.stay()
    waived = ctx.obligation(stay, 2)
    ctx.waive(waived, "5000.00")
    body = ctx.get()
    (row,) = body["upcoming"]["items"]
    assert row["payment_status"] == "paid" and row["is_fully_waived"] is True
    assert row["amount_paid"] == "0.00" and row["outstanding"] == "0.00"
    assert row["effective_amount_due"] == "0.00" and row["amount_adjusted"] == "5000.00"
    ov = body["overview"]
    assert ov["paid"]["obligation_count"] == 1 and ov["due_soon"]["obligation_count"] == 0
    assert ov["total_collected"] == "0.00" and ov["total_outstanding"] == "0.00"
    assert ctx.get(status="paid")["upcoming"]["summary"]["total_paid"] == "0.00"
    assert ctx.get("/api/v1/reports/monthly-collections")["total_collected"] == "0.00"
    assert ctx.get("/api/v1/dashboard/overview")["collected_in_range"] == "0.00"
    (item,) = ctx.get("/api/v1/reports/rent-obligations")["items"]
    assert item["is_fully_waived"] is True and item["amount_paid"] == "0.00"
    # a genuinely paid obligation is not flagged as waived
    _, other = ctx.stay()
    ob = ctx.obligation(other, 2)
    ctx.pay(ob, "5000.00")
    rows = {r["obligation_id"]: r for r in ctx.get()["upcoming"]["items"]}
    assert rows[ob["id"]]["is_fully_waived"] is False
    assert ctx.get()["overview"]["total_collected"] == "5000.00"


def test_balances_match_module7_rules(ctx, db_session):
    from app.models.rent import RentObligation
    from app.services import dashboard_service, rent_service

    ta, sa = ctx.stay()
    tb, sb = ctx.stay()
    obs = [ctx.obligation(sa, 1, period=0), ctx.obligation(sa, 2, period=-40),
           ctx.obligation(sb, 3), ctx.obligation(sb, -2, period=-45)]
    ctx.pay(obs[0], "1200.00")
    ctx.waive(obs[1], "800.00")
    ctx.pay(obs[2], "5000.00")
    ctx.waive(obs[3], "5000.00")
    body = ctx.get()
    models = {str(o.id): o for o in db_session.query(RentObligation).all()}
    rows = body["upcoming"]["items"] + body["overdue"]["items"]
    for r in rows:
        o = models[r["obligation_id"]]
        assert D(r["effective_amount_due"]) == rent_service.effective_due(o)
        assert D(r["outstanding"]) == rent_service.outstanding(o)
        assert r["payment_status"] == dashboard_service.classify(o)
        m7 = rent_service.settlement_status(o, today=stay_rules.business_today())
        if rent_service.effective_due(o) > 0 and o.due_date >= stay_rules.business_today():
            assert r["payment_status"] == m7  # same labels where Module 7 has no overdue/waived
    # SQL aggregates equal the Module 7 python arithmetic over every obligation
    assert D(body["overview"]["total_outstanding"]) == sum(
        rent_service.outstanding(o) for o in models.values())
    assert D(body["overview"]["total_collected"]) == sum(o.amount_paid for o in models.values())


def test_reversed_payment_leaves_its_original_month_and_matches_balances(ctx, db_session):
    _, stay = ctx.stay()
    ob = ctx.obligation(stay, 1)
    old = ctx.pay(ob, "2000.00")
    ctx.pay(ob, "1000.00")
    db_session.execute(text("update rent_payments set paid_on = paid_on - interval '70 days' "
                            "where amount = 2000.00"))
    db_session.commit()
    before = ctx.get("/api/v1/reports/monthly-collections")
    assert before["total_collected"] == "3000.00" and len(before["months"]) == 2
    ctx.reverse(ob, old)
    after = ctx.get("/api/v1/reports/monthly-collections")
    assert after["total_collected"] == "1000.00" and len(after["months"]) == 1
    assert ctx.get()["overview"]["total_collected"] == after["total_collected"]
    assert after["date_field"] == "rent_payments.paid_on"


def test_query_count_does_not_grow_with_rows(ctx):
    from sqlalchemy import event

    from app.db.session import engine

    def measure():
        seen = []

        def spy(conn, cursor, statement, *a):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", spy)
        try:
            ctx.get(page_size=50)
        finally:
            event.remove(engine, "before_cursor_execute", spy)
        return len(seen)

    t, stay = ctx.stay()
    ctx.obligation(stay, 1, period=-1)
    few = measure()
    for i in range(2, 10):
        ctx.obligation(stay, i % 7, period=-i * 40)
    ctx.claim(t, ctx.obligation(stay, 2, period=-900), "10.00")
    assert measure() == few


def test_search_escapes_like_wildcards(client, world):
    ctx = Ctx(client, world)
    _, stay = ctx.stay()
    ctx.obligation(stay, 1)
    assert ctx.get(search="Tina")["upcoming"]["summary"]["obligation_count"] == 1
    assert ctx.get(search="tina t")["upcoming"]["summary"]["obligation_count"] == 1
    for wild in ("_", "%", "T_na", "\\"):
        assert ctx.get(search=wild)["upcoming"]["items"] == [], wild
