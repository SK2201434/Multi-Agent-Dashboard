"""Complaints & issue tracking: tenant portal and owner (own properties)."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_current_tenant, get_db
from app.api.stay_support import ERROR_RESPONSES, Pagination
from app.models.complaint import ComplaintStatusHistory
from app.models.owner import Owner
from app.models.tenant import Tenant
from app.schemas.complaint import (
    ComplaintCategory,
    ComplaintCreate,
    ComplaintResponse,
    ComplaintStatus,
    ComplaintSummary,
    ComplaintVisibility,
    HistoryEntry,
    OwnerComplaintResponse,
    OwnerHistoryEntry,
    OwnerStatusChange,
    Page,
    TenantResolutionNote,
)
from app.services import complaint_service as svc

tenant_router = APIRouter(
    prefix="/api/v1/tenant-portal/complaints",
    tags=["Complaints (Tenant)"],
    responses={**ERROR_RESPONSES, 403: {"description": "Not allowed to perform this action."}},
)
owner_router = APIRouter(
    prefix="/api/v1/properties/{property_id}/complaints",
    tags=["Complaints (Owner)"],
    responses=ERROR_RESPONSES,
)


def tenant_view(row, tenant: Tenant) -> ComplaintResponse:
    complaint, stay, property_name, _name = row
    mine = stay.tenant_id == tenant.id
    return ComplaintResponse(
        id=complaint.id, property_id=stay.property_id, property_name=property_name,
        category=complaint.category, title=complaint.title,
        description=complaint.description, visibility=complaint.visibility,
        status=complaint.status, raised_by_me=mine,
        stay_id=stay.id if mine else None,
        awaiting_my_confirmation=mine and complaint.status == "resolved",
        created_at=complaint.created_at, updated_at=complaint.updated_at,
    )


def owner_view(row) -> OwnerComplaintResponse:
    complaint, stay, property_name, tenant_name = row
    return OwnerComplaintResponse(
        id=complaint.id, property_id=stay.property_id, property_name=property_name,
        stay_id=stay.id, tenant_id=stay.tenant_id, tenant_full_name=tenant_name,
        category=complaint.category, title=complaint.title,
        description=complaint.description, visibility=complaint.visibility,
        status=complaint.status,
        awaiting_tenant_confirmation=complaint.status == "resolved",
        created_at=complaint.created_at, updated_at=complaint.updated_at,
    )


def tenant_history(entry: ComplaintStatusHistory, tenant: Tenant) -> HistoryEntry:
    return HistoryEntry(
        id=entry.id, from_status=entry.from_status, to_status=entry.to_status,
        actor_type=entry.actor_type, by_me=entry.actor_tenant_id == tenant.id,
        note=entry.note, created_at=entry.created_at,
    )


# ------------------------------------------------------------------ tenant
# The tenant always comes from the verified JWT. Complaints are reached only
# through the visibility predicate (own, or public at a property where the
# tenant is currently checked in); anything else is a 404.


@tenant_router.post(
    "", response_model=ComplaintResponse, status_code=status.HTTP_201_CREATED,
    summary="Raise a complaint",
    description=(
        "For one of your own stays that is currently checked in (409 otherwise). "
        "Status starts as `open`. `visibility` (public or private) is fixed at creation. "
        "Property and tenant come from the stay."
    ),
)
def create_complaint(
    data: ComplaintCreate, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_view(svc.create(db, tenant, data), tenant)


@tenant_router.get(
    "", response_model=Page[ComplaintResponse], summary="Complaints I may see",
    description=(
        "My own complaints plus public complaints at properties where I am currently "
        "checked in. Other tenants' private complaints never appear. `mine=true` limits "
        "to my own."
    ),
)
def list_complaints(
    status_filter: ComplaintStatus | None = None,
    category: ComplaintCategory | None = None,
    visibility: ComplaintVisibility | None = None,
    property_id: UUID | None = None, mine: bool = False,
    page: Pagination = Depends(), db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    rows, total = svc.list_for_tenant(
        db, tenant, status=status_filter, category=category, visibility=visibility,
        property_id=property_id, mine=mine, limit=page.limit, offset=page.offset,
    )
    return Page(items=[tenant_view(r, tenant) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@tenant_router.get(
    "/summary", response_model=ComplaintSummary,
    summary="Counts by status over the complaints I may see",
)
def complaint_summary(
    property_id: UUID | None = None, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return svc.summary_for_tenant(db, tenant, property_id)


@tenant_router.get("/{complaint_id}", response_model=ComplaintResponse, summary="One complaint")
def get_complaint(
    complaint_id: UUID, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_view(svc.get_for_tenant(db, tenant, complaint_id), tenant)


@tenant_router.get(
    "/{complaint_id}/history", response_model=list[HistoryEntry],
    summary="Status history (read-only)",
)
def complaint_history(
    complaint_id: UUID, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return [tenant_history(e, tenant) for e in svc.history_for_tenant(db, tenant, complaint_id)]


@tenant_router.post(
    "/{complaint_id}/confirm-resolution", response_model=ComplaintResponse,
    summary="Confirm the issue is fixed (resolved -> closed)",
    description="Only the tenant who raised the complaint (403 for other tenants); only while `resolved` (409).",
)
def confirm_resolution(
    complaint_id: UUID, data: TenantResolutionNote = Body(default_factory=TenantResolutionNote),
    db: Session = Depends(get_db), tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_view(svc.confirm_resolution(db, tenant, complaint_id, data.note), tenant)


@tenant_router.post(
    "/{complaint_id}/reject-resolution", response_model=ComplaintResponse,
    summary="Say the issue is not fixed (resolved -> in_progress)",
    description="Only the tenant who raised the complaint; optional note explains why.",
)
def reject_resolution(
    complaint_id: UUID, data: TenantResolutionNote = Body(default_factory=TenantResolutionNote),
    db: Session = Depends(get_db), tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_view(svc.reject_resolution(db, tenant, complaint_id, data.note), tenant)


# ------------------------------------------------------------------- owner


@owner_router.get(
    "", response_model=Page[OwnerComplaintResponse],
    summary="Complaints at one of my properties (public and private)",
)
def owner_list(
    property_id: UUID, status_filter: ComplaintStatus | None = None,
    category: ComplaintCategory | None = None,
    visibility: ComplaintVisibility | None = None,
    page: Pagination = Depends(), db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    rows, total = svc.list_for_owner(
        db, owner, property_id, status=status_filter, category=category,
        visibility=visibility, limit=page.limit, offset=page.offset,
    )
    return Page(items=[owner_view(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@owner_router.get("/summary", response_model=ComplaintSummary, summary="Counts by status")
def owner_summary(
    property_id: UUID, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return svc.summary_for_owner(db, owner, property_id)


@owner_router.get("/{complaint_id}", response_model=OwnerComplaintResponse, summary="One complaint")
def owner_get(
    property_id: UUID, complaint_id: UUID, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_view(svc.get_for_owner(db, owner, property_id, complaint_id))


@owner_router.get(
    "/{complaint_id}/history", response_model=list[OwnerHistoryEntry],
    summary="Status history (read-only)",
)
def owner_history(
    property_id: UUID, complaint_id: UUID, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return svc.history_for_owner(db, owner, property_id, complaint_id)


@owner_router.post(
    "/{complaint_id}/status", response_model=OwnerComplaintResponse,
    summary="Move a complaint forward (open -> in_progress -> resolved)",
    description=(
        "`in_progress` from `open`, `resolved` from `in_progress`. Setting `resolved` "
        "hands the decision to the tenant who raised it. Owners can never set `closed` "
        "(409) or move a complaint backwards; those are tenant actions."
    ),
)
def owner_change_status(
    property_id: UUID, complaint_id: UUID, data: OwnerStatusChange,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return owner_view(
        svc.change_status(db, owner, property_id, complaint_id, data.status, data.note)
    )
