"""Activation-token lifecycle for owner-created (pending) tenant accounts.

The raw token is returned once to the caller of issue_activation_token and
is never persisted or logged; only its SHA-256 hash is stored. Tokens carry
256 bits of randomness, so an unsalted fast hash is appropriate.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tenant import Tenant
from app.models.tenant_activation_token import TenantActivationToken
from app.services.auth_service import password_hash as password_hasher
from app.services.errors import AppError

INVALID_TOKEN_MESSAGE = "This activation link is invalid or has expired."


class InvalidActivationError(AppError):
    """Deliberately identical for unknown, expired, used and revoked tokens."""

    status_code = 400

    def __init__(self) -> None:
        super().__init__(INVALID_TOKEN_MESSAGE)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_activation_token(db: Session, tenant_id: UUID) -> str:
    """Add a new token row (no commit) and return the raw token once."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)

    db.add(
        TenantActivationToken(
            id=uuid4(),
            tenant_id=tenant_id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=settings.activation_token_expire_hours),
        )
    )
    return raw_token


def revoke_outstanding_tokens(db: Session, tenant_id: UUID) -> None:
    """Revoke every unused, unrevoked token for the tenant (no commit)."""
    db.execute(
        update(TenantActivationToken)
        .where(
            TenantActivationToken.tenant_id == tenant_id,
            TenantActivationToken.consumed_at.is_(None),
            TenantActivationToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )


def activate_account(db: Session, raw_token: str, password: str) -> Tenant:
    """Consume the token and activate the account in one transaction.

    Both steps are conditional UPDATEs, so concurrent or replayed requests
    can succeed at most once: the loser matches zero rows, the whole
    transaction rolls back, and the caller sees the generic invalid-token
    error.
    """
    # Hash the password first: it is slow and needs no database state.
    new_hash = password_hasher.hash(password)
    now = datetime.now(timezone.utc)

    try:
        tenant_id = db.scalar(
            update(TenantActivationToken)
            .where(
                TenantActivationToken.token_hash == hash_token(raw_token),
                TenantActivationToken.consumed_at.is_(None),
                TenantActivationToken.revoked_at.is_(None),
                TenantActivationToken.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(TenantActivationToken.tenant_id)
        )
        if tenant_id is None:
            raise InvalidActivationError()

        activated = db.execute(
            update(Tenant)
            .where(
                Tenant.id == tenant_id,
                Tenant.status == "pending_activation",
                Tenant.password_hash.is_(None),
            )
            .values(
                password_hash=new_hash,
                status="active",
                activated_at=now,
                password_changed_at=now,
            )
            .returning(Tenant.id)
        ).scalar_one_or_none()
        if activated is None:
            raise InvalidActivationError()

        db.commit()
    except Exception:
        db.rollback()
        raise

    return db.get(Tenant, tenant_id)
