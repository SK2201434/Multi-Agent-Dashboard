from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

T = TypeVar("T")

# Timestamps are always rendered in UTC, whatever the DB session zone is.
UtcDatetime = Annotated[
    datetime,
    PlainSerializer(
        lambda value: value.astimezone(timezone.utc), return_type=datetime
    ),
]

StayStatus = Literal[
    "reserved", "checked_in", "checked_out", "cancelled", "no_show"
]
BookingRequestStatus = Literal[
    "pending", "approved", "rejected", "cancelled", "expired"
]
BedChangeStatus = Literal["pending", "approved", "rejected", "cancelled"]


class _StrictRequest(BaseModel):
    """Reject unknown fields so clients cannot smuggle in tenant/status/rent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------ requests


class BookingRequestCreate(_StrictRequest):
    bed_id: UUID
    planned_check_in: date
    # Optional: omit for an open-ended stay.
    planned_check_out: date | None = None
    message: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_dates(self) -> "BookingRequestCreate":
        if (
            self.planned_check_out is not None
            and self.planned_check_out <= self.planned_check_in
        ):
            raise ValueError(
                "planned_check_out must be after planned_check_in."
            )
        return self


class DecisionReason(_StrictRequest):
    reason: str | None = Field(default=None, max_length=500)


class BedChangeRequestCreate(_StrictRequest):
    stay_id: UUID
    to_bed_id: UUID
    reason: str | None = Field(default=None, max_length=500)


class StayActionRequest(_StrictRequest):
    reason: str | None = Field(default=None, max_length=500)


# ----------------------------------------------------------- responses


class BookingRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    bed_id: UUID
    property_id: UUID
    planned_check_in: date
    planned_check_out: date | None
    quoted_monthly_rent: Decimal
    status: BookingRequestStatus
    tenant_message: str | None
    reviewed_at: UtcDatetime | None
    rejection_reason: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class OwnerBookingRequestResponse(BookingRequestResponse):
    tenant_full_name: str
    tenant_email: str
    tenant_phone: str | None
    reviewed_by_owner_id: UUID | None
    stay_id: UUID | None = None


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    bed_id: UUID
    period_start: date
    period_end: date | None
    assigned_at: UtcDatetime
    occupied_from: UtcDatetime | None
    released_at: UtcDatetime | None
    bed_change_request_id: UUID | None


class StayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    booking_request_id: UUID | None
    status: StayStatus
    planned_check_in: date
    planned_check_out: date | None
    actual_check_in_at: UtcDatetime | None
    actual_check_out_at: UtcDatetime | None
    monthly_rent: Decimal
    closed_at: UtcDatetime | None
    close_reason: str | None
    notes: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    # Current bed (or the last one held, for finished stays).
    bed_id: UUID | None = None
    bed_label: str | None = None
    room_number: str | None = None


class StayDetailResponse(StayResponse):
    assignments: list[AssignmentResponse] = []


class OwnerStayResponse(StayResponse):
    tenant_full_name: str


class OwnerStayDetailResponse(OwnerStayResponse):
    assignments: list[AssignmentResponse] = []
    created_by_owner_id: UUID | None
    checked_in_by_owner_id: UUID | None
    checked_out_by_owner_id: UUID | None
    closed_by_owner_id: UUID | None


class BedChangeRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    stay_id: UUID
    property_id: UUID
    from_bed_id: UUID
    to_bed_id: UUID
    status: BedChangeStatus
    reason: str | None
    reviewed_at: UtcDatetime | None
    rejection_reason: str | None
    effective_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class OwnerBedChangeRequestResponse(BedChangeRequestResponse):
    tenant_full_name: str
    reviewed_by_owner_id: UUID | None


# ------------------------------------------------------ public availability


class PublicRoomResponse(BaseModel):
    room_id: UUID
    floor_number: int | None
    room_number: str
    room_type: str
    ac_type: str
    sharing_type: str
    monthly_rent_per_bed: Decimal
    total_bookable_beds: int


class PublicBedAvailability(BaseModel):
    bed_id: UUID
    bed_label: str
    available: bool


class PublicRoomAvailability(PublicRoomResponse):
    available_beds: int
    beds: list[PublicBedAvailability]


class PublicAvailabilityResponse(BaseModel):
    property_id: UUID
    check_in: date
    check_out: date | None
    total_bookable_beds: int
    total_available_beds: int
    rooms: list[PublicRoomAvailability]
