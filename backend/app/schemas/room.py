from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class RoomCreate(BaseModel):
    room_number: str = Field(min_length=1, max_length=30)
    room_type: str = Field(default="standard", min_length=1, max_length=50)
    ac_type: Literal["ac", "non_ac"] = "non_ac"
    sharing_type: Literal["single", "double", "triple", "quad", "other"]

    @field_validator("room_number", "room_type")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


class RoomUpdate(BaseModel):
    room_number: str | None = Field(default=None, min_length=1, max_length=30)
    room_type: str | None = Field(default=None, min_length=1, max_length=50)
    ac_type: Literal["ac", "non_ac"] | None = None
    sharing_type: Literal[
        "single", "double", "triple", "quad", "other"
    ] | None = None
    is_active: bool | None = None

    @field_validator("room_number", "room_type")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    room_number: str
    room_type: str
    ac_type: str
    sharing_type: str
    is_active: bool


RoomCreateRequest = RoomCreate | Annotated[
    list[RoomCreate],
    Field(min_length=1, max_length=100),
]


class RoomCreateBatchResponse(BaseModel):
    created_count: int
    rooms: list[RoomResponse]