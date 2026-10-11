
from uuid import UUID

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

from app.schemas.room import RoomWithBedsCreate, RoomWithBedsResponse

# Number of floors including the ground floor (floor_number 0).
TotalFloors = Annotated[int, Field(ge=1, le=200)]


class PropertyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    property_type: str = Field(min_length=2, max_length=50)
    description: str | None = None
    total_floors: TotalFloors | None = None

    address: str = Field(min_length=3, max_length=255)
    locality: str | None = Field(default=None, max_length=100)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(min_length=3, max_length=10)

    contact_phone: str | None = Field(default=None, max_length=20)
    contact_email: EmailStr | None = None

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    # Optional initial configuration. Omit to create only the property.
    rooms: list[RoomWithBedsCreate] = Field(
        default_factory=list, max_length=500
    )

    @model_validator(mode="after")
    def validate_rooms(self) -> "PropertyCreate":
        numbers = [room.room_number for room in self.rooms]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Duplicate room numbers were provided.")

        for room in self.rooms:
            if room.floor_number is None:
                continue
            if self.total_floors is None:
                raise ValueError(
                    f"Room {room.room_number} has a floor_number but "
                    "total_floors was not provided."
                )
            if room.floor_number >= self.total_floors:
                raise ValueError(
                    f"Room {room.room_number} floor_number "
                    f"{room.floor_number} is outside 0.."
                    f"{self.total_floors - 1}."
                )
        return self


class PropertyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    property_type: str | None = Field(default=None, min_length=2, max_length=50)
    description: str | None = None
    # May be cleared with an explicit null (subject to existing room floors).
    total_floors: TotalFloors | None = None

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

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "PropertyUpdate":
        nullable = {
            "description",
            "locality",
            "contact_phone",
            "contact_email",
            "latitude",
            "longitude",
            "total_floors",
        }
        for field in self.model_fields_set - nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        return self


class PropertyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    property_type: str
    description: str | None
    total_floors: int | None
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


class PropertyCreateResponse(PropertyResponse):
    rooms: list[RoomWithBedsResponse] = []


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
