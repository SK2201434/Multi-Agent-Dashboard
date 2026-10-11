"""Test harness.

Tests run against a dedicated PostgreSQL database, never the development
one. The name is derived from DATABASE_URL (``<name>_test``) unless
TEST_DATABASE_URL is set; either way the database name must contain
"_test" or the run aborts. The schema is built by running the real Alembic
migrations, so the PostgreSQL constraints under test are the shipped ones.
"""

import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import jwt
import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
    else:
        base = os.environ.get("DATABASE_URL") or dotenv_values(
            BACKEND_DIR / ".env"
        ).get("DATABASE_URL")
        assert base, "DATABASE_URL (or TEST_DATABASE_URL) must be configured."
        url = make_url(base)
        url = url.set(database=f"{url.database}_test")
    assert "_test" in (url.database or ""), (
        f"Refusing to run tests against database {url.database!r}: "
        "its name must contain '_test'."
    )
    return url.render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url()

# Must happen before the application (and its engine) is imported.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "development"
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-production")


def _recreate_database(url_string: str) -> None:
    url = make_url(url_string)
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": url.database},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}"'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def alembic_config(url_string: str):
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


@pytest.fixture(scope="session", autouse=True)
def _database():
    from alembic import command

    _recreate_database(TEST_DATABASE_URL)
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    yield


@pytest.fixture(autouse=True)
def _clean_tables(_database):
    yield
    from app.db.session import engine

    with engine.begin() as conn:
        names = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename <> 'alembic_version'"
            )
        ).scalars().all()
        if names:
            conn.execute(
                text(
                    "TRUNCATE "
                    + ", ".join(f'"{n}"' for n in names)
                    + " RESTART IDENTITY CASCADE"
                )
            )


@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# ----------------------------------------------------------------- helpers


class World:
    """Builders for owners, tenants, properties and tokens."""

    PASSWORD = "S3cure-passw0rd!"

    def __init__(self, client):
        self.client = client

    @staticmethod
    def auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def owner(self, label: str = "owner") -> dict:
        email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
        res = self.client.post(
            "/api/v1/auth/register",
            json={"full_name": "Olive Owner", "email": email, "password": self.PASSWORD},
        )
        assert res.status_code == 201, res.text
        res = self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": self.PASSWORD},
        )
        assert res.status_code == 200, res.text
        token = res.json()["access_token"]
        owner_id = jwt.decode(token, options={"verify_signature": False})["sub"]
        return {"id": owner_id, "headers": self.auth(token)}

    def tenant(self, label: str = "tenant") -> dict:
        email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
        res = self.client.post(
            "/api/v1/tenant-auth/register",
            json={"full_name": "Tina Tenant", "email": email, "password": self.PASSWORD},
        )
        assert res.status_code == 201, res.text
        tenant_id = res.json()["id"]
        res = self.client.post(
            "/api/v1/tenant-auth/login",
            json={"email": email, "password": self.PASSWORD},
        )
        assert res.status_code == 200, res.text
        return {
            "id": tenant_id,
            "email": email,
            "headers": self.auth(res.json()["access_token"]),
        }

    def property(self, owner: dict, rooms: int = 1, beds_per_room: int = 2,
                 rent: str = "5000.00") -> dict:
        payload = {
            "name": "Sunrise PG",
            "property_type": "pg",
            "address": "12 Main Road",
            "city": "Hyderabad",
            "state": "Telangana",
            "postal_code": "500001",
            "rooms": [
                {
                    "room_number": f"10{r + 1}",
                    "sharing_type": "double",
                    "monthly_rent_per_bed": rent,
                    "beds": [{"bed_label": f"B{b + 1}"} for b in range(beds_per_room)],
                }
                for r in range(rooms)
            ],
        }
        res = self.client.post("/api/v1/properties", json=payload, headers=owner["headers"])
        assert res.status_code == 201, res.text
        body = res.json()
        beds = [bed for room in body["rooms"] for bed in room["beds"]]
        return {"id": body["id"], "rooms": body["rooms"], "beds": beds}


@pytest.fixture()
def world(client) -> World:
    return World(client)


def days(offset: int) -> str:
    """ISO date `offset` days from today in the business timezone."""
    from app.services.stay_rules import business_today

    return (business_today() + timedelta(days=offset)).isoformat()


def day(offset: int) -> date:
    from app.services.stay_rules import business_today

    return business_today() + timedelta(days=offset)
