"""Owner rent-tracking dashboard, overview and reports. Read-only."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_db
from app.api.stay_support import ERROR_RESPONSES
from app.models.owner import Owner
from app.schemas import dashboard as s
from app.services import dashboard_service as svc

dashboard_router = APIRouter(
    prefix="/api/v1/dashboard", tags=["Dashboard (Owner)"], responses=ERROR_RESPONSES
)
reports_router = APIRouter(
    prefix="/api/v1/reports", tags=["Reports (Owner)"], responses=ERROR_RESPONSES
)

PropertyId = Query(default=None, description="Restrict to one of your properties.")
Search = Query(default=None, min_length=1, max_length=100, description="Tenant name contains.")


@dashboard_router.get(
    "/rent-due", response_model=s.RentDueResponse,
    summary="Rent tracking: upcoming and overdue rent",
    description=(
        "**upcoming**: obligations due from today through today + `within_days` "
        "(inclusive of both ends, business timezone). With `status=all` it lists "
        "every payment status, paid ones included; `status` narrows it. "
        "**overdue**: due before today with a balance left, never limited by the "
        "window or hidden by it; `status` applies here too, so `status=paid` gives "
        "an empty overdue list. Overdue is a date class, not a payment status.\n\n"
        "**overview** (cards) honours `property_id` and `search` but ignores `status`. "
        "`due_soon` = in-window obligations that still owe money (paid ones excluded); "
        "`paid`/`partially_paid`/`unpaid` describe all in-window obligations; "
        "`total_collected`/`total_outstanding` cover every obligation in scope.\n\n"
        "Tenant counts are distinct tenants, obligation counts are rows. Paid = verified "
        "payments net of reversals; pending/rejected claims never count and are reported "
        "in `pending_claims`. Fully waived rent is `paid` (`is_fully_waived=true`) but is "
        "not cash: `amount_paid` stays 0 and it never adds to `total_collected`."
    ),
)
def rent_due(
    within_days: int = Query(default=7, ge=1, le=svc.MAX_WITHIN_DAYS),
    status: s.RentStatusFilter = "all",
    property_id: UUID | None = PropertyId,
    search: str | None = Search,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=svc.MAX_PAGE_SIZE),
    overdue_page: int = Query(default=1, ge=1),
    overdue_page_size: int = Query(default=20, ge=1, le=svc.MAX_PAGE_SIZE),
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return svc.rent_due(
        db, owner, within_days=within_days, status=status, property_id=property_id,
        search=search, page=page, page_size=page_size,
        overdue_page=overdue_page, overdue_page_size=overdue_page_size,
    )


@dashboard_router.get(
    "/overview", response_model=s.OwnerOverviewResponse,
    summary="Compact owner overview",
    description=(
        "Occupancy is a snapshot of current bed assignments (no history is kept). "
        "`collected_in_range` sums verified, non-reversed payments by `paid_on`; "
        "the range defaults to the current month."
    ),
)
def overview(
    property_id: UUID | None = PropertyId, from_date: date | None = None,
    to_date: date | None = None, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return svc.owner_overview(
        db, owner, property_id=property_id, from_date=from_date, to_date=to_date
    )


@reports_router.get(
    "/monthly-collections", response_model=s.MonthlyCollectionsReport,
    summary="Verified collections by month (by payment date, reversals excluded)",
    description=(
        "Groups verified payments by `paid_on`. Reversed payments are excluded, so a "
        "month's total can fall after a later reversal (reversal dates are not reported). "
        "Waived rent is never cash. Totals equal the sum of `amount_paid`."
    ),
)
def monthly_collections(
    property_id: UUID | None = PropertyId, from_date: date | None = None,
    to_date: date | None = None, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return svc.monthly_collections(
        db, owner, property_id=property_id, from_date=from_date, to_date=to_date
    )


@reports_router.get(
    "/collection-trends", response_model=s.CollectionTrendsReport,
    summary="Monthly collection totals, zero-filled, with month-over-month change",
)
def collection_trends(
    property_id: UUID | None = PropertyId, from_date: date | None = None,
    to_date: date | None = None, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return svc.collection_trends(
        db, owner, property_id=property_id, from_date=from_date, to_date=to_date
    )


@reports_router.get(
    "/property-rent-summary", response_model=s.PropertyRentSummaryReport,
    summary="Collected, outstanding and overdue rent per property",
)
def property_rent_summary(
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return svc.property_rent_summary(db, owner)


@reports_router.get(
    "/rent-obligations", response_model=s.ObligationReport,
    summary="Paginated rent obligations with status",
)
def obligation_report(
    status: s.RentStatusFilter = "all",
    property_id: UUID | None = PropertyId,
    search: str | None = Search,
    due_from: date | None = None,
    due_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=svc.MAX_PAGE_SIZE),
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return svc.obligation_report(
        db, owner, status=status, property_id=property_id, search=search,
        due_from=due_from, due_to=due_to, page=page, page_size=page_size,
    )
