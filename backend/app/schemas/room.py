from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.bed import BedCreate, BedResponse

AcType = Literal["ac", "non_ac"]
SharingType = Literal["single", "double", "triple", "quad", "other"]

# Floor 0 is the ground floor. Keep in sync with the DB check constraints.
MAX_FLOOR_NUMBER = 199

# Up to 10 digits and 2 decimals, matching Numeric(10, 2).
RentPerBed = Annotated[
    Decimal, Field(gt=0, max_digits=10, decimal_places=2)
]
FloorNumber = Annotated[int, Field(ge=0, le=MAX_FLOOR_NUMBER)]


def _clean_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("This field cannot be blank.")
    return value


class RoomCreate(BaseModel):
    floor_number: FloorNumber | None = None
    room_number: str = Field(min_length=1, max_length=30)
    room_type: str = Field(default="standard", min_length=1, max_length=50)
    ac_type: AcType = "non_ac"
    sharing_type: SharingType
    monthly_rent_per_bed: RentPerBed

    @field_validator("room_number", "room_type")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _clean_text(value)


class RoomWithBedsCreate(RoomCreate):
    """Room definition used inside a nested property-creation request."""

    beds: list[BedCreate] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def reject_duplicate_bed_labels(self) -> "RoomWithBedsCreate":
        labels = [bed.bed_label for bed in self.beds]
        if len(labels) != len(set(labels)):
            raise ValueError(
                f"Room {self.room_number} has duplicate bed labels."
            )
        return self


class RoomUpdate(BaseModel):
    floor_number: FloorNumber | None = None
    room_number: str | None = Field(default=None, min_length=1, max_length=30)
    room_type: str | None = Field(default=None, min_length=1, max_length=50)
    ac_type: AcType | None = None
    sharing_type: SharingType | None = None
    monthly_rent_per_bed: RentPerBed | None = None
    is_active: bool | None = None

    @field_validator("room_number", "room_type")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value)

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "RoomUpdate":
        # floor_number may be cleared with an explicit null; the rest may not.
        for field in self.model_fields_set - {"floor_number"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        return self


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    floor_number: int | None
    room_number: str
    room_type: str
    ac_type: str
    sharing_type: str
    monthly_rent_per_bed: Decimal | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RoomWithBedsResponse(RoomResponse):
    beds: list[BedResponse] = []


RoomCreateRequest = RoomCreate | Annotated[
    list[RoomCreate],
    Field(min_length=1, max_length=100),
]


class RoomCreateBatchResponse(BaseModel):
    created_count: int
    rooms: list[RoomResponse]
