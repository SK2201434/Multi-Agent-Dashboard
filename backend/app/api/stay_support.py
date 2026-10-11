"""Shared helpers for the Module 6 routers: pagination, responses, errors."""

from fastapi import Query

from app.schemas.stay import (
    OwnerBedChangeRequestResponse,
    OwnerBookingRequestResponse,
    OwnerStayDetailResponse,
    OwnerStayResponse,
    StayDetailResponse,
    StayResponse,
)
from app.services.stay_service import StayView

ERROR_RESPONSES = {
    401: {"description": "Missing or invalid authentication."},
    404: {"description": "Resource not found or not accessible."},
    409: {"description": "State conflict (overlap, stale or duplicate action)."},
    422: {"description": "Invalid request data."},
}


class Pagination:
    def __init__(
        self,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


def _stay_fields(view: StayView) -> dict:
    data = StayResponse.model_validate(view.stay).model_dump()
    data.update(
        bed_id=view.bed_id,
        bed_label=view.bed_label,
        room_number=view.room_number,
    )
    return data


def tenant_stay(view: StayView) -> StayResponse:
    return StayResponse(**_stay_fields(view))


def tenant_stay_detail(view: StayView) -> StayDetailResponse:
    return StayDetailResponse(**_stay_fields(view), assignments=view.assignments)


def owner_stay(view: StayView) -> OwnerStayResponse:
    return OwnerStayResponse(**_stay_fields(view), tenant_full_name=view.tenant_name)


def owner_stay_detail(view: StayView) -> OwnerStayDetailResponse:
    stay = view.stay
    return OwnerStayDetailResponse(
        **_stay_fields(view),
        tenant_full_name=view.tenant_name,
        assignments=view.assignments,
        created_by_owner_id=stay.created_by_owner_id,
        checked_in_by_owner_id=stay.checked_in_by_owner_id,
        checked_out_by_owner_id=stay.checked_out_by_owner_id,
        closed_by_owner_id=stay.closed_by_owner_id,
    )


def owner_booking_request(row) -> OwnerBookingRequestResponse:
    request, tenant, stay_id = row
    return OwnerBookingRequestResponse(
        **{
            k: getattr(request, k)
            for k in (
                "id", "tenant_id", "bed_id", "property_id",
                "planned_check_in", "planned_check_out",
                "quoted_monthly_rent", "status", "tenant_message",
                "reviewed_at", "rejection_reason", "created_at",
                "updated_at", "reviewed_by_owner_id",
            )
        },
        tenant_full_name=tenant.full_name,
        tenant_email=tenant.email,
        tenant_phone=tenant.phone,
        stay_id=stay_id,
    )


def owner_bed_change(row) -> OwnerBedChangeRequestResponse:
    request, tenant_name = row
    return OwnerBedChangeRequestResponse(
        **{
            k: getattr(request, k)
            for k in (
                "id", "tenant_id", "stay_id", "property_id", "from_bed_id",
                "to_bed_id", "status", "reason", "reviewed_at",
                "rejection_reason", "effective_at", "created_at",
                "updated_at", "reviewed_by_owner_id",
            )
        },
        tenant_full_name=tenant_name,
    )
