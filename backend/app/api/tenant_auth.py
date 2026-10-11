from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant, get_db
from app.models.tenant import Tenant
from app.schemas.auth import TokenResponse
from app.schemas.property import PublicPropertyResponse
from app.schemas.tenant import (
    ActivationRequest,
    PasswordChangeRequest,
    TenantLoginRequest,
    TenantProfileResponse,
    TenantRegisterRequest,
    TenantSelfUpdate,
)
from app.services import activation_service, tenant_auth_service
from app.services.auth_service import create_access_token


router = APIRouter(
    prefix="/api/v1/tenant-auth",
    tags=["Tenant Authentication"],
)


def _token_for(tenant: Tenant) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            str(tenant.id),
            role="tenant",
            token_version=tenant.token_version,
        )
    )


@router.post(
    "/register",
    response_model=TenantProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    data: TenantRegisterRequest,
    db: Session = Depends(get_db),
):
    return tenant_auth_service.register_tenant(db, data)


@router.post("/login", response_model=TokenResponse)
def login(
    data: TenantLoginRequest,
    db: Session = Depends(get_db),
):
    tenant = tenant_auth_service.authenticate_tenant(
        db, data.email, data.password
    )

    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _token_for(tenant)


@router.post("/activate", response_model=TokenResponse)
def activate(
    data: ActivationRequest,
    db: Session = Depends(get_db),
):
    tenant = activation_service.activate_account(
        db, data.token, data.password
    )
    return _token_for(tenant)


@router.get("/me", response_model=TenantProfileResponse)
def get_me(tenant: Tenant = Depends(get_current_tenant)):
    return tenant


@router.patch("/me", response_model=TenantProfileResponse)
def update_me(
    data: TenantSelfUpdate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_auth_service.update_profile(db, tenant, data)


@router.post("/me/password", response_model=TokenResponse)
def change_password(
    data: PasswordChangeRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Change password; earlier tokens stop working, a fresh one is returned."""
    tenant = tenant_auth_service.change_password(db, tenant, data)
    return _token_for(tenant)


@router.get("/me/property", response_model=PublicPropertyResponse)
def get_my_property(
    property_id: UUID | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_auth_service.resolve_current_property(
        db, tenant, property_id
    )
