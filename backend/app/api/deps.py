
import uuid
from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.owner import Owner
from app.models.tenant import Tenant


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


bearer_scheme = HTTPBearer(auto_error=False)


def _decode_token(
    credentials: HTTPAuthorizationCredentials | None,
    expected_role: str,
) -> tuple[uuid.UUID, dict]:
    """Validate a bearer token and require the expected role claim.

    Owner and tenant tokens are not interchangeable: the role claim must
    match the dependency being used.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat"]},
        )

        subject = payload.get("sub")
        if not isinstance(subject, str):
            raise ValueError("Missing token subject")

        if payload.get("role") != expected_role:
            raise ValueError("Wrong token role")

        return uuid.UUID(subject), payload

    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_owner(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    db: Session = Depends(get_db),
) -> Owner:
    owner_id, _ = _decode_token(credentials, "owner")

    owner = db.scalar(
        select(Owner).where(Owner.id == owner_id)
    )

    if owner is None or not owner.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Owner account is unavailable.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return owner


def get_current_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    db: Session = Depends(get_db),
) -> Tenant:
    tenant_id, payload = _decode_token(credentials, "tenant")

    tenant = db.get(Tenant, tenant_id)

    unavailable = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Tenant account is unavailable.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if (
        tenant is None
        or tenant.status != "active"
        or tenant.password_hash is None
    ):
        raise unavailable

    # Reject tokens issued before the last password change.
    token_version = payload.get("tv")
    if (
        not isinstance(token_version, int)
        or isinstance(token_version, bool)
        or token_version != tenant.token_version
    ):
        raise unavailable

    return tenant
