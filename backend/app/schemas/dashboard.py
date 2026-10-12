from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

RentStatusFilter = Literal["all", "unpaid", "partially_paid", "paid"]
RentPaymentStatus = Literal["unpaid", "partially_paid", "paid"]
RentTiming = Literal["upcoming", "overdue"]


class Counts(BaseModel):
    """Distinct tenants and rent obligations are different numbers: one
    tenant can owe several obligations."""

    tenant_count: int
    obligation_count: int


class RentSummary(Counts):
    # Sums of the obligations counted above (original / effective / verified
    # net of reversals / remaining).
    total_amount: Decimal
    total_effective_due: Decimal
    total_paid: Decimal
    total_outstanding: Decimal


class StatusBreakdown(BaseModel):
    unpaid: Counts
    partially_paid: Counts
    paid: Counts


class AppliedFilters(BaseModel):
    within_days: int
    status: RentStatusFilter
    property_id: UUID | None
    search: str | None
    today: date
    window_start: date
    window_end: date
    timezone: str


class PageMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class RentRow(BaseModel):
    obligation_id: UUID
    stay_id: UUID
    tenant_id: UUID
    tenant_name: str
    tenant_email: str
    tenant_phone: str | None
    property_id: UUID
    property_name: str
    room_number: str | None
    bed_label: str | None
    period_start: date
    period_end: date
    due_date: date
    amount_due: Decimal
    amount_adjusted: Decimal
    effective_amount_due: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    payment_status: RentPaymentStatus
    # True when waivers cover the whole amount: classified "paid" (nothing
    # left to collect) although no cash was received (amount_paid == 0).
    is_fully_waived: bool
    timing: RentTiming
    days_until_due: int | None
    days_overdue: int | None
    has_pending_claim: bool


class RentList(BaseModel):
    summary: RentSummary
    status_breakdown: StatusBreakdown
    items: list[RentRow]
    pagination: PageMeta


class RentOverview(BaseModel):
    """Headline numbers. ``due_soon`` counts obligations still owing money
    that are due in the window; paid/partially_paid/unpaid describe all
    obligations due in the window; ``overdue`` is independent of the window.
    ``total_*`` cover every obligation in scope regardless of due date."""

    due_soon: Counts
    paid: Counts
    partially_paid: Counts
    unpaid: Counts
    overdue: Counts
    total_collected: Decimal
    total_outstanding: Decimal
    total_overdue_amount: Decimal


class PendingClaims(BaseModel):
    """Awaiting owner verification. Never part of any paid amount."""

    claim_count: int
    tenant_count: int
    total_claimed_amount: Decimal


class RentDueResponse(BaseModel):
    filters: AppliedFilters
    overview: RentOverview
    pending_claims: PendingClaims
    upcoming: RentList
    overdue: RentList


# ----------------------------------------------------------- owner overview


class OccupancyOverview(BaseModel):
    """Snapshot as of now from current bed assignments (no history)."""

    total_beds: int
    occupied_beds: int
    reserved_beds: int
    vacant_beds: int
    unavailable_beds: int
    occupancy_percentage: Decimal


class OwnerOverviewResponse(BaseModel):
    today: date
    property_id: UUID | None
    properties: int
    rooms: int
    occupancy: OccupancyOverview
    collected_from: date
    collected_to: date
    collected_in_range: Decimal
    total_outstanding: Decimal
    total_overdue: Decimal
    pending_claims: PendingClaims


# ------------------------------------------------------------------ reports


class MonthlyCollection(BaseModel):
    month: date  # first day of the month
    payment_count: int
    total_collected: Decimal


class MonthlyCollectionsReport(BaseModel):
    """Verified, non-reversed payments grouped by their ``paid_on`` date."""

    date_field: str
    from_date: date
    to_date: date
    property_id: UUID | None
    total_collected: Decimal
    months: list[MonthlyCollection]


class TrendPoint(BaseModel):
    month: date
    total_collected: Decimal
    change_from_previous: Decimal | None


class CollectionTrendsReport(BaseModel):
    date_field: str
    from_date: date
    to_date: date
    property_id: UUID | None
    total_collected: Decimal
    points: list[TrendPoint]


class PropertyRentSummary(BaseModel):
    property_id: UUID
    property_name: str
    obligation_count: int
    tenant_count: int
    total_effective_due: Decimal
    collected: Decimal
    outstanding: Decimal
    overdue: Decimal
    overdue_obligation_count: int


class PropertyRentSummaryReport(BaseModel):
    today: date
    items: list[PropertyRentSummary]


class ObligationReportRow(BaseModel):
    obligation_id: UUID
    tenant_id: UUID
    tenant_name: str
    property_id: UUID
    property_name: str
    period_start: date
    period_end: date
    due_date: date
    amount_due: Decimal
    effective_amount_due: Decimal
    amount_paid: Decimal
    outstanding: Decimal
    payment_status: RentPaymentStatus
    is_fully_waived: bool
    timing: Literal["upcoming", "overdue", "settled"]


class ObligationReport(BaseModel):
    summary: RentSummary
    items: list[ObligationReportRow]
    pagination: PageMeta
