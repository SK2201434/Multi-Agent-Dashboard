from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.property import Property
from app.models.property_tenant import PropertyTenant
from app.models.tenant import Tenant
from app.schemas.tenant import (
    PasswordChangeRequest,
    TenantRegisterRequest,
    TenantSelfUpdate,
)
from app.services.auth_service import password_hash as password_hasher
from app.services.errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
)

# Same message for active and pending accounts so registration does not
# reveal whether an owner has invited this address.
REGISTRATION_CONFLICT_MESSAGE = (
    "An account with these details already exists. If you were invited "
    "by a property owner, use the activation link you were sent."
)

# Verified against when the email is unknown, to even out login timing.
_DUMMY_HASH = password_hasher.hash("timing-equalisation-placeholder")


def register_tenant(db: Session, data: TenantRegisterRequest) -> Tenant:
    now = datetime.now(timezone.utc)
    tenant = Tenant(
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        password_hash=password_hasher.hash(data.password),
        status="active",
        activated_at=now,
        password_changed_at=now,
    )

    try:
        db.add(tenant)
        db.commit()
    except IntegrityError as exc:
        # The unique email index is the race-proof duplicate guard.
        db.rollback()
        raise ConflictError(REGISTRATION_CONFLICT_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise

    return tenant


def authenticate_tenant(
    db: Session, email: str, password: str
) -> Tenant | None:
    tenant = db.scalar(select(Tenant).where(Tenant.email == email))

    stored_hash = tenant.password_hash if tenant else None
    password_ok = password_hasher.verify(password, stored_hash or _DUMMY_HASH)

    if tenant is None or stored_hash is None or not password_ok:
        return None

    if tenant.status != "active":
        return None

    return tenant


def update_profile(
    db: Session, tenant: Tenant, data: TenantSelfUpdate
) -> Tenant:
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(tenant, name, value)

    try:
        db.commit()
        db.refresh(tenant)
    except Exception:
        db.rollback()
        raise

    return tenant


def change_password(
    db: Session, tenant: Tenant, data: PasswordChangeRequest
) -> Tenant:
    if tenant.password_hash is None or not password_hasher.verify(
        data.current_password, tenant.password_hash
    ):
        raise UnauthorizedError("Current password is incorrect.")

    new_hash = password_hasher.hash(data.new_password)

    try:
        # One conditional UPDATE: bumps the version atomically and only if
        # the password is still the one that was just verified, so two
        # concurrent changes cannot both succeed.
        new_version = db.scalar(
            update(Tenant)
            .where(
                Tenant.id == tenant.id,
                Tenant.password_hash == tenant.password_hash,
            )
            .values(
                password_hash=new_hash,
                password_changed_at=datetime.now(timezone.utc),
                token_version=Tenant.token_version + 1,
            )
            .returning(Tenant.token_version)
        )
        if new_version is None:
            raise ConflictError(
                "Password was changed concurrently. Please try again."
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(tenant)
    return tenant


def resolve_current_property(
    db: Session,
    tenant: Tenant,
    property_id: UUID | None = None,
) -> Property:
    """Pick the property a tenant is acting in.

    Until Stay Management exists there is no reliable "current" property,
    so an explicit property_id is required whenever the tenant has more
    than one active association. Never guesses.
    """
    properties = list(
        db.scalars(
            select(Property)
            .join(PropertyTenant, PropertyTenant.property_id == Property.id)
            .where(
                PropertyTenant.tenant_id == tenant.id,
                PropertyTenant.status == "active",
                Property.deleted_at.is_(None),
            )
            .order_by(Property.name)
        ).all()
    )

    if property_id is not None:
        for property_obj in properties:
            if property_obj.id == property_id:
                return property_obj
        raise NotFoundError("Property not found.")

    if not properties:
        raise NotFoundError("You are not associated with any property.")

    if len(properties) > 1:
        raise ConflictError(
            "You are associated with multiple properties. "
            "Specify property_id."
        )

    return properties[0]
