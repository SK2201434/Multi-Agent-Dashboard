"""Reviews & ratings: tenant (own reviews) and owner (own properties)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_current_tenant, get_db
from app.api.stay_support import ERROR_RESPONSES, Pagination
from app.models.owner import Owner
from app.models.tenant import Tenant
from app.schemas.review import (
    OwnerResponseUpsert,
    OwnerReviewResponse,
    Page,
    RatingSummary,
    ReviewCreate,
    ReviewResponse,
    ReviewUpdate,
)
from app.services import review_service as svc

tenant_router = APIRouter(
    prefix="/api/v1/tenant-portal/reviews",
    tags=["Reviews (Tenant)"],
    responses=ERROR_RESPONSES,
)
owner_property_router = APIRouter(
    prefix="/api/v1/properties/{property_id}",
    tags=["Reviews (Owner)"],
    responses=ERROR_RESPONSES,
)
owner_router = APIRouter(
    prefix="/api/v1/reviews", tags=["Reviews (Owner)"], responses=ERROR_RESPONSES
)


def tenant_review(row) -> ReviewResponse:
    review, stay, property_name, _tenant_name = row
    return ReviewResponse(
        **{k: getattr(review, k) for k in (
            "id", "stay_id", "rating", "comment", "owner_response",
            "owner_responded_at", "edited_at", "created_at", "updated_at")},
        property_id=stay.property_id, property_name=property_name,
    )


def owner_review(row) -> OwnerReviewResponse:
    _review, stay, _property_name, tenant_name = row
    check_in, check_out = svc.stay_dates(stay)
    return OwnerReviewResponse(
        **tenant_review(row).model_dump(),
        tenant_id=stay.tenant_id, tenant_full_name=tenant_name,
        stay_check_in=check_in, stay_check_out=check_out,
    )


# ------------------------------------------------------------------ tenant
# The tenant always comes from the verified JWT; no field names another.


@tenant_router.post(
    "", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED,
    summary="Review a stay",
    description=(
        "Only for one of your own stays with an actual check-in (checked in or "
        "checked out); a reservation alone does not qualify (409). One review per "
        "stay (409 if it exists). Rating is an integer 1-5."
    ),
)
def create_review(
    data: ReviewCreate, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_review(svc.create_review(db, tenant, data))


@tenant_router.get("", response_model=Page[ReviewResponse], summary="List my reviews")
def my_reviews(
    property_id: UUID | None = None, page: Pagination = Depends(),
    db: Session = Depends(get_db), tenant: Tenant = Depends(get_current_tenant),
):
    rows, total = svc.list_for_tenant(db, tenant, property_id, page.limit, page.offset)
    return Page(items=[tenant_review(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@tenant_router.get("/{review_id}", response_model=ReviewResponse, summary="One of my reviews")
def my_review(
    review_id: UUID, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_review(svc.get_for_tenant(db, tenant, review_id))


@tenant_router.patch(
    "/{review_id}", response_model=ReviewResponse,
    summary="Edit my rating and/or comment",
    description="The owner response, if any, is kept.",
)
def edit_review(
    review_id: UUID, data: ReviewUpdate, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return tenant_review(svc.update_review(db, tenant, review_id, data))


@tenant_router.delete(
    "/{review_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete my review",
    description="Also removes the owner response. The stay can then be reviewed again.",
)
def delete_review(
    review_id: UUID, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    svc.delete_review(db, tenant, review_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------- owner


@owner_router.get(
    "", response_model=Page[OwnerReviewResponse],
    summary="Reviews of my properties (newest first)",
    description="Optionally filter by one of your properties, rating, or whether it has a response.",
)
def owner_reviews(
    property_id: UUID | None = None,
    rating: int | None = Query(default=None, ge=1, le=5),
    has_response: bool | None = None,
    page: Pagination = Depends(), db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    rows, total = svc.list_for_owner(
        db, owner, property_id, rating, has_response, page.limit, page.offset
    )
    return Page(items=[owner_review(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@owner_property_router.get(
    "/reviews", response_model=Page[OwnerReviewResponse], summary="Reviews of one property"
)
def property_reviews(
    property_id: UUID,
    rating: int | None = Query(default=None, ge=1, le=5),
    has_response: bool | None = None,
    page: Pagination = Depends(), db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    rows, total = svc.list_for_owner(
        db, owner, property_id, rating, has_response, page.limit, page.offset
    )
    return Page(items=[owner_review(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@owner_property_router.get(
    "/review-summary", response_model=RatingSummary,
    summary="Average rating, total and per-star counts for a property",
)
def review_summary(
    property_id: UUID, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return svc.summary(db, owner, property_id)


@owner_property_router.get(
    "/reviews/{review_id}", response_model=OwnerReviewResponse, summary="One review"
)
def get_review(
    property_id: UUID, review_id: UUID, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_review(svc.get_for_owner(db, owner, property_id, review_id))


@owner_property_router.put(
    "/reviews/{review_id}/response", response_model=OwnerReviewResponse,
    summary="Respond to a review (or replace your response)",
    description="Only the response is written; the rating and text cannot be changed.",
)
def respond_to_review(
    property_id: UUID, review_id: UUID, data: OwnerResponseUpsert,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return owner_review(svc.respond(db, owner, property_id, review_id, data.response))


@owner_property_router.delete(
    "/reviews/{review_id}/response", response_model=OwnerReviewResponse,
    summary="Remove my response",
)
def remove_response(
    property_id: UUID, review_id: UUID, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return owner_review(svc.respond(db, owner, property_id, review_id, None))
