"""Owner-side Module 6 endpoints, scoped to a property the owner owns."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_db
from app.api.stay_support import (
    ERROR_RESPONSES,
    Pagination,
    owner_bed_change,
    owner_booking_request,
    owner_stay,
    owner_stay_detail,
)
from app.models.owner import Owner
from app.schemas.stay import (
    BedChangeStatus,
    BookingRequestStatus,
    DecisionReason,
    OwnerBedChangeRequestResponse,
    OwnerBookingRequestResponse,
    OwnerStayDetailResponse,
    OwnerStayResponse,
    Page,
    StayActionRequest,
    StayStatus,
)
from app.services import bed_change_service, booking_service, stay_service

booking_router = APIRouter(
    prefix="/api/v1/properties/{property_id}/booking-requests",
    tags=["Booking Requests (Owner)"],
    responses=ERROR_RESPONSES,
)
stay_router = APIRouter(
    prefix="/api/v1/properties/{property_id}/stays",
    tags=["Stays (Owner)"],
    responses=ERROR_RESPONSES,
)
bed_change_router = APIRouter(
    prefix="/api/v1/properties/{property_id}/bed-change-requests",
    tags=["Bed Change Requests (Owner)"],
    responses=ERROR_RESPONSES,
)


# ------------------------------------------------------- booking requests


@booking_router.get(
    "",
    response_model=Page[OwnerBookingRequestResponse],
    summary="List booking requests for a property",
)
def list_booking_requests(
    property_id: UUID,
    status_filter: BookingRequestStatus | None = Query(
        default=None, alias="status"
    ),
    page: Pagination = Depends(),
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    rows, total = booking_service.list_for_owner(
        db, owner, property_id, status_filter, page.limit, page.offset
    )
    return Page(
        items=[owner_booking_request(r) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@booking_router.get(
    "/{request_id}",
    response_model=OwnerBookingRequestResponse,
    summary="Get a booking request",
)
def get_booking_request(
    property_id: UUID,
    request_id: UUID,
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_booking_request(
        booking_service.get_for_owner(db, owner, property_id, request_id)
    )


@booking_router.post(
    "/{request_id}/approve",
    response_model=OwnerStayDetailResponse,
    summary="Approve a booking request",
    description=(
        "Atomically re-validates availability, creates the reservation "
        "with its rent snapshot and planned bed assignment, and marks the "
        "request approved. 409 if the bed or tenant is no longer free."
    ),
)
def approve_booking_request(
    property_id: UUID,
    request_id: UUID,
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    stay = booking_service.approve(db, owner, property_id, request_id)
    return owner_stay_detail(
        stay_service.get_for_owner(db, owner, property_id, stay.id)
    )


@booking_router.post(
    "/{request_id}/reject",
    response_model=OwnerBookingRequestResponse,
    summary="Reject a booking request",
)
def reject_booking_request(
    property_id: UUID,
    request_id: UUID,
    data: DecisionReason = Body(default_factory=DecisionReason),
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    booking_service.reject(db, owner, property_id, request_id, data.reason)
    return owner_booking_request(
        booking_service.get_for_owner(db, owner, property_id, request_id)
    )


# ------------------------------------------------------------------ stays


@stay_router.get(
    "",
    response_model=Page[OwnerStayResponse],
    summary="List reservations and stays for a property",
)
def list_stays(
    property_id: UUID,
    status_filter: StayStatus | None = Query(default=None, alias="status"),
    tenant_id: UUID | None = None,
    page: Pagination = Depends(),
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    views, total = stay_service.list_for_owner(
        db, owner, property_id, status_filter, tenant_id,
        page.limit, page.offset,
    )
    return Page(
        items=[owner_stay(v) for v in views],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@stay_router.get(
    "/{stay_id}",
    response_model=OwnerStayDetailResponse,
    summary="Get a stay with its bed-assignment history",
)
def get_stay(
    property_id: UUID,
    stay_id: UUID,
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_stay_detail(
        stay_service.get_for_owner(db, owner, property_id, stay_id)
    )


@stay_router.post(
    "/{stay_id}/check-in",
    response_model=OwnerStayDetailResponse,
    summary="Check a reserved tenant in",
)
def check_in(
    property_id: UUID,
    stay_id: UUID,
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_stay_detail(
        stay_service.check_in(db, owner, property_id, stay_id)
    )


@stay_router.post(
    "/{stay_id}/check-out",
    response_model=OwnerStayDetailResponse,
    summary="Record actual check-out",
    description=(
        "Releases the bed. Until this is called a checked-in stay keeps "
        "occupying its bed, even past its planned check-out date."
    ),
)
def check_out(
    property_id: UUID,
    stay_id: UUID,
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_stay_detail(
        stay_service.check_out(db, owner, property_id, stay_id)
    )


@stay_router.post(
    "/{stay_id}/cancel",
    response_model=OwnerStayDetailResponse,
    summary="Cancel a reservation (before check-in only)",
)
def cancel_stay(
    property_id: UUID,
    stay_id: UUID,
    data: StayActionRequest = Body(default_factory=StayActionRequest),
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_stay_detail(
        stay_service.cancel(db, owner, property_id, stay_id, data.reason)
    )


@stay_router.post(
    "/{stay_id}/no-show",
    response_model=OwnerStayDetailResponse,
    summary="Mark a reservation as a no-show",
    description="A deliberate owner action; never applied automatically.",
)
def mark_no_show(
    property_id: UUID,
    stay_id: UUID,
    data: StayActionRequest = Body(default_factory=StayActionRequest),
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_stay_detail(
        stay_service.mark_no_show(db, owner, property_id, stay_id, data.reason)
    )


# ------------------------------------------------------ bed-change requests


@bed_change_router.get(
    "",
    response_model=Page[OwnerBedChangeRequestResponse],
    summary="List bed-change requests for a property",
)
def list_bed_change_requests(
    property_id: UUID,
    status_filter: BedChangeStatus | None = Query(default=None, alias="status"),
    page: Pagination = Depends(),
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    rows, total = bed_change_service.list_for_owner(
        db, owner, property_id, status_filter, page.limit, page.offset
    )
    return Page(
        items=[owner_bed_change(r) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@bed_change_router.get(
    "/{request_id}",
    response_model=OwnerBedChangeRequestResponse,
    summary="Get a bed-change request",
)
def get_bed_change_request(
    property_id: UUID,
    request_id: UUID,
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_bed_change(
        bed_change_service.get_for_owner(db, owner, property_id, request_id)
    )


@bed_change_router.post(
    "/{request_id}/approve",
    response_model=OwnerBedChangeRequestResponse,
    summary="Approve a bed change",
    description=(
        "Re-validates the destination under locks, then ends the old "
        "assignment and starts the new one in one transaction. Rent is "
        "preserved. 409 if the destination is no longer free."
    ),
)
def approve_bed_change(
    property_id: UUID,
    request_id: UUID,
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    bed_change_service.approve(db, owner, property_id, request_id)
    return owner_bed_change(
        bed_change_service.get_for_owner(db, owner, property_id, request_id)
    )


@bed_change_router.post(
    "/{request_id}/reject",
    response_model=OwnerBedChangeRequestResponse,
    summary="Reject a bed change",
)
def reject_bed_change(
    property_id: UUID,
    request_id: UUID,
    data: DecisionReason = Body(default_factory=DecisionReason),
    db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    bed_change_service.reject(db, owner, property_id, request_id, data.reason)
    return owner_bed_change(
        bed_change_service.get_for_owner(db, owner, property_id, request_id)
    )
