
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)


class PropertyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    property_type: str = Field(min_length=2, max_length=50)
    description: str | None = None

    address: str = Field(min_length=3, max_length=255)
    locality: str | None = Field(default=None, max_length=100)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(min_length=3, max_length=10)

    contact_phone: str | None = Field(default=None, max_length=20)
    contact_email: EmailStr | None = None

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class PropertyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    property_type: str | None = Field(default=None, min_length=2, max_length=50)
    description: str | None = None

    address: str | None = Field(default=None, min_length=3, max_length=255)
    locality: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    state: str | None = Field(default=None, min_length=2, max_length=100)
    postal_code: str | None = Field(default=None, min_length=3, max_length=10)

    contact_phone: str | None = Field(default=None, max_length=20)
    contact_email: EmailStr | None = None

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    is_active: bool | None = None


class PropertyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    property_type: str
    description: str | None
    address: str
    locality: str | None
    city: str
    state: str
    postal_code: str
    contact_phone: str | None
    contact_email: str | None
    latitude: float | None
    longitude: float | None
    is_active: bool


class PublicPropertyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    property_type: str
    description: str | None
    address: str
    locality: str | None
    city: str
    state: str
    postal_code: str
    latitude: float | None
    longitude: float | None


class NearbyPropertyResponse(PublicPropertyResponse):
    distance_km: float
