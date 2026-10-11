from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

BedStatus = Literal["available", "maintenance", "inactive"]


def _clean_label(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("bed_label cannot be blank.")
    return value


class BedCreate(BaseModel):
    bed_label: str = Field(min_length=1, max_length=30)
    status: BedStatus = "available"

    _strip_label = field_validator("bed_label")(_clean_label)


class BedUpdate(BaseModel):
    bed_label: str | None = Field(default=None, min_length=1, max_length=30)
    status: BedStatus | None = None

    @field_validator("bed_label")
    @classmethod
    def strip_label(cls, value: str | None) -> str | None:
        return None if value is None else _clean_label(value)

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "BedUpdate":
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        return self


class BedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    room_id: UUID
    bed_label: str
    status: str
    created_at: datetime
    updated_at: datetime


BedCreateRequest = BedCreate | Annotated[
    list[BedCreate],
    Field(min_length=1, max_length=50),
]
