"""Upgrade/downgrade of the Module 6 migration on a throwaway database.

Runs Alembic in a subprocess against its own ``*_test_migration`` database,
so it never touches the development database or the shared test database.
"""

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import BACKEND_DIR, TEST_DATABASE_URL, _recreate_database

PARENT = "d2b8f6a40c71"
MODULE6 = "f5a1c8d3e962"
HEAD = "a9c4e7b2d150"


@pytest.fixture()
def mig_url():
    url = make_url(TEST_DATABASE_URL)
    url = url.set(database=f"{url.database}_migration")
    assert "_test" in url.database
    rendered = url.render_as_string(hide_password=False)
    _recreate_database(rendered)
    yield rendered


def alembic(url, *args, check=True):
    env = dict(os.environ, DATABASE_URL=url)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    if check:
        assert result.returncode == 0, result.stderr[-2000:]
    return result


def seed_legacy(conn):
    owner, prop, room, bed1, bed2 = (str(uuid.uuid4()) for _ in range(5))
    tenant = str(uuid.uuid4())
    conn.execute(text("insert into owners (id, full_name, email, password_hash, is_active) "
                      "values (:o, 'o', 'o@x.com', 'h', true)"), {"o": owner})
    conn.execute(text("insert into properties (id, owner_id, name, property_type, address, city, state, postal_code, is_active) "
                      "values (:p, :o, 'p', 'pg', 'a', 'c', 's', '500001', true)"), {"p": prop, "o": owner})
    conn.execute(text("insert into rooms (id, property_id, room_number, sharing_type, room_type, ac_type, is_active) "
                      "values (:r, :p, '1', 'double', 'standard', 'non_ac', true)"), {"r": room, "p": prop})
    for bed, label in ((bed1, "A"), (bed2, "B")):
        conn.execute(text("insert into beds (id, room_id, bed_label, status) values (:b, :r, :l, 'available')"),
                     {"b": bed, "r": room, "l": label})
    conn.execute(text("insert into tenants (id, full_name, email, status) values (:t, 't', 't@x.com', 'pending_activation')"),
                 {"t": tenant})
    conn.execute(text("insert into stays (id, tenant_id, bed_id, move_in_date, move_out_date, monthly_rent) values "
                      "(:a, :t, :b1, '2026-01-01', NULL, 3000), "
                      "(:b, :t, :b2, '2025-01-01', '2025-06-01', 2500), "
                      "(:c, :t, :b2, '2025-07-01', '2025-07-01', 2500)"),
                 {"a": str(uuid.uuid4()), "b": str(uuid.uuid4()), "c": str(uuid.uuid4()),
                  "t": tenant, "b1": bed1, "b2": bed2})
    return bed1


def test_single_head_and_chain():
    result = subprocess.run([sys.executable, "-m", "alembic", "heads"],
                            cwd=BACKEND_DIR, capture_output=True, text=True,
                            env=dict(os.environ, DATABASE_URL=TEST_DATABASE_URL))
    heads = [line for line in result.stdout.splitlines() if "(head)" in line]
    assert heads == [f"{HEAD} (head)"]


def test_legacy_rows_survive_upgrade_and_downgrade_round_trips(mig_url):
    alembic(mig_url, "upgrade", PARENT)
    engine = create_engine(mig_url)
    with engine.begin() as conn:
        bed1 = seed_legacy(conn)

    alembic(mig_url, "upgrade", HEAD)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "select status, planned_check_in, planned_check_out, monthly_rent, "
            "actual_check_out_at is not null from stays order by planned_check_in")).all()
        assert [r[0] for r in rows] == ["checked_out", "checked_out", "checked_in"]
        assert str(rows[0][1]) == "2025-01-01" and str(rows[0][2]) == "2025-06-01"
        assert rows[1][2] is None  # same-day legacy stay: no planned checkout kept
        assert rows[2][2] is None and rows[2][4] is False
        assert [float(r[3]) for r in rows] == [2500.0, 2500.0, 3000.0]
        current = conn.execute(text(
            "select bed_id::text, occupied_from is not null from stay_bed_assignments "
            "where released_at is null")).all()
        assert current == [(bed1, True)]
        assert conn.execute(text("select count(*) from stay_bed_assignments")).scalar() == 3

    alembic(mig_url, "downgrade", PARENT)
    with engine.connect() as conn:
        cols = {r[0] for r in conn.execute(text(
            "select column_name from information_schema.columns where table_name='stays'"))}
        assert {"bed_id", "move_in_date", "move_out_date"} <= cols
        assert "status" not in cols
        assert conn.execute(text("select count(*) from stays where move_out_date is null")).scalar() == 1
        assert conn.execute(text("select count(*) from information_schema.tables "
                                 "where table_name in ('booking_requests','stay_bed_assignments',"
                                 "'bed_change_requests')")).scalar() == 0

    alembic(mig_url, "upgrade", HEAD)  # and forward again
    engine.dispose()


def test_downgrade_refuses_to_destroy_module6_history(mig_url):
    alembic(mig_url, "upgrade", HEAD)
    engine = create_engine(mig_url)
    with engine.begin() as conn:
        owner, prop, room, bed, tenant = (str(uuid.uuid4()) for _ in range(5))
        conn.execute(text("insert into owners (id, full_name, email, password_hash, is_active) values (:o,'o','o@x.com','h',true)"), {"o": owner})
        conn.execute(text("insert into properties (id, owner_id, name, property_type, address, city, state, postal_code, is_active) "
                          "values (:p,:o,'p','pg','a','c','s','500001',true)"), {"p": prop, "o": owner})
        conn.execute(text("insert into rooms (id, property_id, room_number, sharing_type, room_type, ac_type, is_active) "
                          "values (:r,:p,'1','double','standard','non_ac',true)"), {"r": room, "p": prop})
        conn.execute(text("insert into beds (id, room_id, bed_label, status) values (:b,:r,'A','available')"), {"b": bed, "r": room})
        conn.execute(text("insert into tenants (id, full_name, email, status) values (:t,'t','t@x.com','pending_activation')"), {"t": tenant})
        conn.execute(text("insert into booking_requests (id, tenant_id, bed_id, property_id, planned_check_in, "
                          "quoted_monthly_rent, status) values (gen_random_uuid(), :t, :b, :p, '2030-01-01', 1, 'pending')"),
                     {"t": tenant, "b": bed, "p": prop})

    result = alembic(mig_url, "downgrade", PARENT, check=False)
    assert result.returncode != 0
    assert "Refusing to downgrade" in result.stderr
    with engine.connect() as conn:  # nothing was dropped
        assert conn.execute(text("select count(*) from booking_requests")).scalar() == 1
    engine.dispose()


def test_payments_downgrade_refuses_to_drop_financial_or_notification_data(mig_url):
    alembic(mig_url, "upgrade", HEAD)
    engine = create_engine(mig_url)
    with engine.begin() as conn:
        owner = str(uuid.uuid4())
        conn.execute(text("insert into owners (id, full_name, email, password_hash, is_active) "
                          "values (:o,'o','o@x.com','h',true)"), {"o": owner})
        conn.execute(text("insert into notifications (id, owner_id, kind, title, body, dedupe_key) "
                          "values (gen_random_uuid(), :o, 'k', 't', 'b', 'd')"), {"o": owner})
    result = alembic(mig_url, "downgrade", MODULE6, check=False)
    assert result.returncode != 0 and "Refusing to downgrade" in result.stderr
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from notifications")).scalar() == 1
    engine.dispose()
