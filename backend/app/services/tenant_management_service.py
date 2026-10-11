"""Owner-side tenant management.

Authority model: an owner may only act on tenants associated with a
property they own, and may edit a tenant's personal details only while the
account is pending activation. Once the tenant has activated, the account
belongs to the tenant; the owner can only end their own property's
association. Every rule is checked against database state here, never
against request input.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.owner import Owner
from app.models.property_tenant import PropertyTenant
from app.models.tenant import Tenant
from app.models.tenant_activation_token import TenantActivationToken
from app.schemas.tenant import OwnerTenantCreate, PendingTenantUpdate
from app.services import activation_service
from app.services.access import require_manageable_property
from app.services.errors import ConflictError, ForbiddenError, NotFoundError

EMAIL_EXISTS_MESSAGE = (
    "A tenant account with this email already exists. Linking an "
    "existing account to a property requires the tenant's verified "
    "consent, which is not available yet."
)


@dataclass
class TenantAtProperty:
    tenant: Tenant
    association: PropertyTenant
    # Only set when dev token exposure is enabled; see dev_token().
    dev_activation_token: str | None = None


def dev_token(raw_token: str | None) -> str | None:
    """Return the raw token only for explicit local development."""
    settings = get_settings()
    if (
        raw_token is not None
        and settings.dev_expose_activation_tokens
        and settings.environment == "development"
    ):
        return raw_token
    return None


def _find_association(
    db: Session,
    owner: Owner,
    property_id: UUID,
    tenant_id: UUID,
    lock_tenant: bool = False,
) -> tuple[Tenant, PropertyTenant]:
    """Load a tenant through an active association at the owner's property.

    404 for anything the owner cannot see, whether it does not exist or
    belongs to someone else.
    """
    property_obj = require_manageable_property(db, owner, property_id)

    statement = (
        select(Tenant, PropertyTenant)
        .join(PropertyTenant, PropertyTenant.tenant_id == Tenant.id)
        .where(
            Tenant.id == tenant_id,
            PropertyTenant.property_id == property_obj.id,
            PropertyTenant.status == "active",
        )
    )
    if lock_tenant:
        # Serialises with a concurrent activation, which updates this row.
        statement = statement.with_for_update(of=Tenant)

    row = db.execute(statement).first()
    if row is None:
        raise NotFoundError("Tenant not found.")
    return row[0], row[1]


def create_tenant(
    db: Session,
    owner: Owner,
    property_id: UUID,
    data: OwnerTenantCreate,
) -> TenantAtProperty:
    property_obj = require_manageable_property(db, owner, property_id)

    tenant = Tenant(
        id=uuid4(),
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        status="pending_activation",
    )
    association = PropertyTenant(
        id=uuid4(),
        tenant_id=tenant.id,
        property_id=property_obj.id,
        created_by_owner_id=owner.id,
    )

    try:
        db.add(tenant)
        db.flush()
        db.add(association)
        raw_token = activation_service.issue_activation_token(db, tenant.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(EMAIL_EXISTS_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise

    return TenantAtProperty(tenant, association, dev_token(raw_token))


def list_tenants(
    db: Session,
    owner: Owner,
    property_id: UUID,
) -> list[TenantAtProperty]:
    """Active associations only: once ended, an owner no longer sees the
    tenant's personal details."""
    property_obj = require_manageable_property(db, owner, property_id)

    statement = (
        select(Tenant, PropertyTenant)
        .join(PropertyTenant, PropertyTenant.tenant_id == Tenant.id)
        .where(
            PropertyTenant.property_id == property_obj.id,
            PropertyTenant.status == "active",
        )
        .order_by(PropertyTenant.created_at.desc())
    )

    return [
        TenantAtProperty(tenant, association)
        for tenant, association in db.execute(statement).all()
    ]


def get_tenant(
    db: Session, owner: Owner, property_id: UUID, tenant_id: UUID
) -> TenantAtProperty:
    tenant, association = _find_association(db, owner, property_id, tenant_id)
    return TenantAtProperty(tenant, association)


def update_pending_tenant(
    db: Session,
    owner: Owner,
    property_id: UUID,
    tenant_id: UUID,
    data: PendingTenantUpdate,
) -> TenantAtProperty:
    try:
        tenant, association = _find_association(
            db, owner, property_id, tenant_id, lock_tenant=True
        )

        if tenant.status != "pending_activation":
            raise ForbiddenError(
                "This tenant has activated their account and manages "
                "their own profile. It cannot be edited by an owner."
            )

        changes = data.model_dump(exclude_unset=True)
        email_changed = (
            "email" in changes and changes["email"] != tenant.email
        )
        for name, value in changes.items():
            setattr(tenant, name, value)

        raw_token = None
        if email_changed:
            # The earlier invitation went to the old address; replace it.
            activation_service.revoke_outstanding_tokens(db, tenant.id)
            raw_token = activation_service.issue_activation_token(
                db, tenant.id
            )

        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(EMAIL_EXISTS_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise

    return TenantAtProperty(tenant, association, dev_token(raw_token))


def end_association(
    db: Session, owner: Owner, property_id: UUID, tenant_id: UUID
) -> str:
    """End the tenant's association with this owner's property.

    Never deletes an activated account. A pending invitation that has no
    other active association is withdrawn entirely, since that account was
    never claimed by anyone and holds only data the owner entered.
    """
    try:
        tenant, association = _find_association(
            db, owner, property_id, tenant_id, lock_tenant=True
        )

        association.status = "ended"
        association.ended_at = datetime.now(timezone.utc)
        outcome = "association_ended"

        if tenant.status == "pending_activation":
            activation_service.revoke_outstanding_tokens(db, tenant.id)

            db.flush()
            other_active = db.scalar(
                select(PropertyTenant.id).where(
                    PropertyTenant.tenant_id == tenant.id,
                    PropertyTenant.status == "active",
                )
            )
            if other_active is None:
                db.execute(
                    delete(TenantActivationToken).where(
                        TenantActivationToken.tenant_id == tenant.id
                    )
                )
                db.execute(
                    delete(PropertyTenant).where(
                        PropertyTenant.tenant_id == tenant.id
                    )
                )
                db.execute(delete(Tenant).where(Tenant.id == tenant.id))
                outcome = "invitation_cancelled"

        db.commit()
    except Exception:
        db.rollback()
        raise

    return outcome
