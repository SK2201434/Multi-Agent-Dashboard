from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant, get_db
from app.api.stay_support import (
    ERROR_RESPONSES,
    Pagination,
    tenant_stay,
    tenant_stay_detail,
)
from app.models.tenant import Tenant
from app.schemas.stay import (
    BedChangeRequestCreate,
    BedChangeRequestResponse,
    BedChangeStatus,
    BookingRequestCreate,
    BookingRequestResponse,
    BookingRequestStatus,
    Page,
    StayDetailResponse,
    StayResponse,
    StayStatus,
)
from app.services import bed_change_service, booking_service, stay_service

router = APIRouter(
    prefix="/api/v1/tenant-portal",
    tags=["Tenant Portal - Stays"],
    responses=ERROR_RESPONSES,
)

# The tenant is always taken from the verified JWT (get_current_tenant);
# no path or body field can name another tenant.


@router.post(
    "/booking-requests",
    response_model=BookingRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a bed",
    description=(
        "Creates a pending booking request. It reserves nothing and does "
        "not reduce availability. The check-out date is optional "
        "(open-ended stay). The rent is set by the property, not the client."
    ),
)
def create_booking_request(
    data: BookingRequestCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return booking_service.create_request(db, tenant, data)


@router.get(
    "/booking-requests",
    response_model=Page[BookingRequestResponse],
    summary="List my booking requests",
)
def list_booking_requests(
    status_filter: BookingRequestStatus | None = Query(
        default=None, alias="status"
    ),
    page: Pagination = Depends(),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    items, total = booking_service.list_for_tenant(
        db, tenant, status_filter, page.limit, page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/booking-requests/{request_id}",
    response_model=BookingRequestResponse,
    summary="Get one of my booking requests",
)
def get_booking_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return booking_service.get_for_tenant(db, tenant, request_id)


@router.post(
    "/booking-requests/{request_id}/cancel",
    response_model=BookingRequestResponse,
    summary="Cancel a pending booking request",
)
def cancel_booking_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return booking_service.cancel_by_tenant(db, tenant, request_id)


@router.get(
    "/stays",
    response_model=Page[StayResponse],
    summary="List my reservations and stays",
)
def list_my_stays(
    status_filter: StayStatus | None = Query(default=None, alias="status"),
    page: Pagination = Depends(),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    views, total = stay_service.list_for_tenant(
        db, tenant, status_filter, page.limit, page.offset
    )
    return Page(
        items=[tenant_stay(v) for v in views],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/stays/{stay_id}",
    response_model=StayDetailResponse,
    summary="Get one of my stays, with bed history",
)
def get_my_stay(
    stay_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_stay_detail(stay_service.get_for_tenant(db, tenant, stay_id))


@router.post(
    "/bed-change-requests",
    response_model=BedChangeRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a move to another bed",
    description=(
        "Same property only. A pending request does not reserve the "
        "destination or change the current bed."
    ),
)
def create_bed_change_request(
    data: BedChangeRequestCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return bed_change_service.create_request(db, tenant, data)


@router.get(
    "/bed-change-requests",
    response_model=Page[BedChangeRequestResponse],
    summary="List my bed-change requests",
)
def list_bed_change_requests(
    status_filter: BedChangeStatus | None = Query(default=None, alias="status"),
    page: Pagination = Depends(),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    items, total = bed_change_service.list_for_tenant(
        db, tenant, status_filter, page.limit, page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/bed-change-requests/{request_id}",
    response_model=BedChangeRequestResponse,
    summary="Get one of my bed-change requests",
)
def get_bed_change_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return bed_change_service.get_for_tenant(db, tenant, request_id)


@router.post(
    "/bed-change-requests/{request_id}/cancel",
    response_model=BedChangeRequestResponse,
    summary="Cancel a pending bed-change request",
)
def cancel_bed_change_request(
    request_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return bed_change_service.cancel_by_tenant(db, tenant, request_id)
