from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

from app.core.identity import normalize_email, normalize_phone

NormalizedEmail = Annotated[EmailStr, AfterValidator(normalize_email)]
NormalizedPhone = Annotated[
    str, Field(max_length=30), AfterValidator(normalize_phone)
]
Password = Annotated[str, Field(min_length=8, max_length=128)]
FullName = Annotated[str, Field(min_length=2, max_length=150)]


class _StrictRequest(BaseModel):
    """Reject unknown fields so clients cannot smuggle in role/status."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _reject_null_fields(model: BaseModel, nullable: set[str]) -> None:
    for name in model.model_fields_set - nullable:
        if getattr(model, name) is None:
            raise ValueError(f"{name} cannot be null.")


# ---------- tenant self-service ----------


class TenantRegisterRequest(_StrictRequest):
    full_name: FullName
    email: NormalizedEmail
    phone: NormalizedPhone | None = None
    password: Password


class TenantLoginRequest(_StrictRequest):
    email: NormalizedEmail
    password: Annotated[str, Field(min_length=1, max_length=128)]


class TenantProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    email: str
    phone: str | None
    status: str
    activated_at: datetime | None
    created_at: datetime


class TenantSelfUpdate(_StrictRequest):
    """Email is not self-editable until email verification exists."""

    full_name: FullName | None = None
    phone: NormalizedPhone | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "TenantSelfUpdate":
        _reject_null_fields(self, nullable={"phone"})
        return self


class PasswordChangeRequest(_StrictRequest):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Password

    @model_validator(mode="after")
    def require_different_password(self) -> "PasswordChangeRequest":
        if self.new_password == self.current_password:
            raise ValueError(
                "New password must differ from the current password."
            )
        return self


class ActivationRequest(_StrictRequest):
    token: Annotated[str, Field(min_length=20, max_length=200)]
    password: Password


# ---------- owner-managed tenants ----------


class OwnerTenantCreate(_StrictRequest):
    full_name: FullName
    email: NormalizedEmail
    phone: NormalizedPhone | None = None


class PendingTenantUpdate(_StrictRequest):
    full_name: FullName | None = None
    email: NormalizedEmail | None = None
    phone: NormalizedPhone | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "PendingTenantUpdate":
        _reject_null_fields(self, nullable={"phone"})
        return self


class PropertyTenantResponse(BaseModel):
    """What an owner may see about a tenant at their property."""

    tenant_id: UUID
    full_name: str
    email: str
    phone: str | None
    account_status: str
    association_id: UUID
    association_status: str
    associated_at: datetime
    # Development only; see DEV_EXPOSE_ACTIVATION_TOKENS. Always null in
    # production and on every read endpoint.
    dev_activation_token: str | None = None


class AssociationEndResponse(BaseModel):
    tenant_id: UUID
    property_id: UUID
    outcome: Literal["association_ended", "invitation_cancelled"]
