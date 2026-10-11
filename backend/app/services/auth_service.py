
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.owner import Owner


password_hash = PasswordHash.recommended()


def register_owner(
    db: Session,
    full_name: str,
    email: str,
    password: str,
) -> Owner:
    normalized_email = email.strip().lower()

    existing_owner = db.scalar(
        select(Owner).where(Owner.email == normalized_email)
    )

    if existing_owner:
        raise ValueError("An account with this email already exists.")

    owner = Owner(
        id=uuid.uuid4(),
        full_name=full_name.strip(),
        email=normalized_email,
        password_hash=password_hash.hash(password),
    )

    db.add(owner)

    try:
        db.commit()
        db.refresh(owner)
    except Exception:
        db.rollback()
        raise

    return owner


def authenticate_owner(
    db: Session,
    email: str,
    password: str,
) -> Owner | None:
    normalized_email = email.strip().lower()

    owner = db.scalar(
        select(Owner).where(Owner.email == normalized_email)
    )

    if owner is None:
        return None

    if not password_hash.verify(password, owner.password_hash):
        return None

    if not owner.is_active:
        return None

    return owner


def create_access_token(owner_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload = {
        "sub": owner_id,
        "iat": now,
        "exp": now + timedelta(
            minutes=settings.access_token_expire_minutes
        ),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
