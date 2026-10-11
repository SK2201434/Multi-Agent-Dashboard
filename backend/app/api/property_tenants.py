from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_db
from app.models.owner import Owner
from app.schemas.tenant import (
    AssociationEndResponse,
    OwnerTenantCreate,
    PendingTenantUpdate,
    PropertyTenantResponse,
)
from app.services import tenant_management_service as service
from app.services.tenant_management_service import TenantAtProperty


router = APIRouter(
    prefix="/api/v1/properties/{property_id}/tenants",
    tags=["Property Tenants"],
)


def _to_response(item: TenantAtProperty) -> PropertyTenantResponse:
    return PropertyTenantResponse(
        tenant_id=item.tenant.id,
        full_name=item.tenant.full_name,
        email=item.tenant.email,
        phone=item.tenant.phone,
        account_status=item.tenant.status,
        association_id=item.association.id,
        association_status=item.association.status,
        associated_at=item.association.created_at,
        dev_activation_token=item.dev_activation_token,
    )


@router.post(
    "",
    response_model=PropertyTenantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tenant(
    property_id: UUID,
    data: OwnerTenantCreate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return _to_response(
        service.create_tenant(db, current_owner, property_id, data)
    )


@router.get("", response_model=list[PropertyTenantResponse])
def list_tenants(
    property_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return [
        _to_response(item)
        for item in service.list_tenants(db, current_owner, property_id)
    ]


@router.get("/{tenant_id}", response_model=PropertyTenantResponse)
def get_tenant(
    property_id: UUID,
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    return _to_response(
        service.get_tenant(db, current_owner, property_id, tenant_id)
    )


@router.patch("/{tenant_id}", response_model=PropertyTenantResponse)
def update_pending_tenant(
    property_id: UUID,
    tenant_id: UUID,
    data: PendingTenantUpdate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    """Correct a pending tenant's details. 403 once the tenant activated."""
    return _to_response(
        service.update_pending_tenant(
            db, current_owner, property_id, tenant_id, data
        )
    )


@router.delete("/{tenant_id}", response_model=AssociationEndResponse)
def end_association(
    property_id: UUID,
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    """End this property's association; never deletes an active account."""
    outcome = service.end_association(
        db, current_owner, property_id, tenant_id
    )
    return AssociationEndResponse(
        tenant_id=tenant_id, property_id=property_id, outcome=outcome
    )
