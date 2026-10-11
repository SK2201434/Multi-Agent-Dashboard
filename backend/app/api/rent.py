"""Rent obligations, payment claims, verified payments and notifications."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_current_tenant, get_db
from app.api.stay_support import ERROR_RESPONSES, Pagination
from app.models.owner import Owner
from app.models.rent import PaymentClaim, RentObligation, RentPayment
from app.models.tenant import Tenant
from app.schemas.rent import (
    AdjustmentCreate,
    AdjustmentResponse,
    ClaimRejection,
    ClaimResponse,
    ClaimStatus,
    NotificationResponse,
    ObligationCreate,
    ObligationDetailResponse,
    ObligationResponse,
    OwnerClaimResponse,
    OwnerObligationResponse,
    OwnerPaymentCreate,
    Page,
    PaymentClaimCreate,
    PaymentResponse,
    ReversalRequest,
)
from app.services import notification_service as notes
from app.services import rent_service as svc
from app.services import stay_rules as rules

owner_router = APIRouter(
    prefix="/api/v1/properties/{property_id}",
    tags=["Rent & Payments (Owner)"],
    responses=ERROR_RESPONSES,
)
tenant_router = APIRouter(
    prefix="/api/v1/tenant-portal",
    tags=["Rent & Payments (Tenant)"],
    responses=ERROR_RESPONSES,
)
owner_notifications_router = APIRouter(
    prefix="/api/v1/notifications", tags=["Notifications (Owner)"], responses=ERROR_RESPONSES
)
tenant_notifications_router = APIRouter(
    prefix="/api/v1/tenant-portal/notifications",
    tags=["Notifications (Tenant)"],
    responses=ERROR_RESPONSES,
)


# ---------------------------------------------------------------- builders


def _obligation_fields(o: RentObligation) -> dict:
    return dict(
        id=o.id, stay_id=o.stay_id, tenant_id=o.tenant_id, property_id=o.property_id,
        period_start=o.period_start, period_end=o.period_end, due_date=o.due_date,
        amount_due=o.amount_due, amount_adjusted=o.amount_adjusted,
        effective_amount_due=svc.effective_due(o), amount_paid=o.amount_paid,
        outstanding=svc.outstanding(o), payment_status=svc.settlement_status(o),
        created_at=o.created_at,
    )


def obligation(o: RentObligation) -> ObligationResponse:
    return ObligationResponse(**_obligation_fields(o))


def owner_obligation(row) -> OwnerObligationResponse:
    o, name = row
    return OwnerObligationResponse(**_obligation_fields(o), tenant_full_name=name)


def detail(result) -> ObligationDetailResponse:
    o, payments, claims, adjustments = result
    return ObligationDetailResponse(
        **_obligation_fields(o),
        payments=[payment(p, reason, reversed_=reason is not None) for p, reason in payments],
        claims=[ClaimResponse.model_validate(c) for c in claims],
        adjustments=[adjustment(a, reason, reason is not None) for a, reason in adjustments],
    )


def adjustment(a, reason=None, reversed_=False) -> AdjustmentResponse:
    data = AdjustmentResponse.model_validate(a)
    data.reversed = reversed_
    data.reversal_reason = reason
    return data


def payment(p: RentPayment, reason=None, reversed_=False) -> PaymentResponse:
    data = PaymentResponse.model_validate(p)
    data.reversed = reversed_
    data.reversal_reason = reason
    return data


def owner_claim(row) -> OwnerClaimResponse:
    claim, name = row
    return OwnerClaimResponse(
        **ClaimResponse.model_validate(claim).model_dump(),
        property_id=claim.property_id,
        decided_by_owner_id=claim.decided_by_owner_id,
        tenant_full_name=name,
    )


# ------------------------------------------------------------------- owner


@owner_router.post(
    "/stays/{stay_id}/rent-obligations",
    response_model=ObligationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a rent obligation for a stay period",
    description="Amount defaults to the stay's agreed rent snapshot.",
)
def create_obligation(
    property_id: UUID, stay_id: UUID, data: ObligationCreate,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return obligation(svc.create_obligation(db, owner, property_id, stay_id, data))


@owner_router.get(
    "/rent-obligations", response_model=Page[OwnerObligationResponse],
    summary="List rent obligations (verified balances)",
)
def list_obligations(
    property_id: UUID, stay_id: UUID | None = None, only_open: bool = False,
    page: Pagination = Depends(), db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    rows, total = svc.list_obligations_for_owner(
        db, owner, property_id, stay_id, only_open, page.limit, page.offset
    )
    return Page(items=[owner_obligation(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@owner_router.get(
    "/rent-obligations/{obligation_id}", response_model=ObligationDetailResponse,
    summary="Obligation with its verified payments and claims",
)
def get_obligation(
    property_id: UUID, obligation_id: UUID,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return detail(svc.get_detail_for_owner(db, owner, property_id, obligation_id))


@owner_router.post(
    "/rent-obligations/{obligation_id}/payments", response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment the owner has verified (e.g. cash)",
)
def record_payment(
    property_id: UUID, obligation_id: UUID, data: OwnerPaymentCreate,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return payment(svc.record_owner_payment(db, owner, property_id, obligation_id, data))


@owner_router.post(
    "/rent-obligations/{obligation_id}/payments/{payment_id}/reverse",
    response_model=PaymentResponse,
    summary="Reverse a verified payment (audited correction)",
    description="Never deletes: adds a reversal record and restores the balance.",
)
def reverse_payment(
    property_id: UUID, obligation_id: UUID, payment_id: UUID, data: ReversalRequest,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    p = svc.reverse_payment(db, owner, property_id, obligation_id, payment_id, data.reason)
    return payment(p, data.reason.strip(), reversed_=True)


@owner_router.post(
    "/rent-obligations/{obligation_id}/adjustments", response_model=AdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Waive or reduce outstanding rent (audited)",
    description=(
        "Reduces the effective amount due; the original amount is preserved. "
        "Only the outstanding part can be waived (409 otherwise): verified "
        "payments are never turned into a credit."
    ),
)
def adjust_obligation(
    property_id: UUID, obligation_id: UUID, data: AdjustmentCreate,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return adjustment(
        svc.adjust_obligation(db, owner, property_id, obligation_id, data.amount, data.reason)
    )


@owner_router.post(
    "/rent-obligations/{obligation_id}/adjustments/{adjustment_id}/reverse",
    response_model=AdjustmentResponse,
    summary="Reverse an adjustment (audited; restores the amount due)",
)
def reverse_adjustment(
    property_id: UUID, obligation_id: UUID, adjustment_id: UUID, data: ReversalRequest,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    a = svc.reverse_adjustment(
        db, owner, property_id, obligation_id, adjustment_id, data.reason
    )
    return adjustment(a, data.reason.strip(), True)


@owner_router.get(
    "/payment-claims", response_model=Page[OwnerClaimResponse],
    summary="List payment claims for a property",
)
def list_claims(
    property_id: UUID, status_filter: ClaimStatus | None = Query(default=None, alias="status"),
    page: Pagination = Depends(), db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    rows, total = svc.list_claims_for_owner(
        db, owner, property_id, status_filter, page.limit, page.offset
    )
    return Page(items=[owner_claim(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


@owner_router.post(
    "/rent-obligations/{obligation_id}/payment-claims/{claim_id}/confirm",
    response_model=ClaimResponse,
    summary="Confirm a payment claim",
    description=(
        "Creates the verified payment and updates the balance exactly once, "
        "under a lock on the obligation. 409 if already decided or if the "
        "claim exceeds the outstanding balance."
    ),
)
def confirm_claim(
    property_id: UUID, obligation_id: UUID, claim_id: UUID,
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return svc.confirm_claim(db, owner, property_id, obligation_id, claim_id)


@owner_router.post(
    "/rent-obligations/{obligation_id}/payment-claims/{claim_id}/reject",
    response_model=ClaimResponse,
    summary="Reject a payment claim (balance unchanged)",
)
def reject_claim(
    property_id: UUID, obligation_id: UUID, claim_id: UUID,
    data: ClaimRejection = Body(default_factory=ClaimRejection),
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    return svc.reject_claim(db, owner, property_id, obligation_id, claim_id, data.reason)


# ------------------------------------------------------------------ tenant


@tenant_router.get(
    "/rent-obligations", response_model=Page[ObligationResponse],
    summary="My rent obligations",
)
def my_obligations(
    page: Pagination = Depends(), db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    rows, total = svc.list_obligations_for_tenant(db, tenant, page.limit, page.offset)
    return Page(items=[obligation(o) for o in rows], total=total,
                limit=page.limit, offset=page.offset)


@tenant_router.get(
    "/rent-obligations/{obligation_id}", response_model=ObligationDetailResponse,
    summary="One of my obligations, with payments and claims",
)
def my_obligation(
    obligation_id: UUID, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return detail(svc.get_detail_for_tenant(db, tenant, obligation_id))


@tenant_router.post(
    "/rent-obligations/{obligation_id}/payment-claims", response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Claim that I paid (pending owner confirmation)",
    description=(
        "A claim is not a payment: it does not change the balance and does "
        "not stop reminders until the owner confirms it."
    ),
)
def submit_claim(
    obligation_id: UUID, data: PaymentClaimCreate,
    db: Session = Depends(get_db), tenant: Tenant = Depends(get_current_tenant),
):
    return svc.submit_claim(db, tenant, obligation_id, data)


@tenant_router.get(
    "/payment-claims", response_model=Page[ClaimResponse], summary="My payment claims"
)
def my_claims(
    status_filter: ClaimStatus | None = Query(default=None, alias="status"),
    page: Pagination = Depends(), db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    rows, total = svc.list_claims_for_tenant(db, tenant, status_filter, page.limit, page.offset)
    return Page(items=rows, total=total, limit=page.limit, offset=page.offset)


@tenant_router.get(
    "/payment-claims/{claim_id}", response_model=ClaimResponse, summary="One of my claims"
)
def my_claim(
    claim_id: UUID, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return svc.get_claim_for_tenant(db, tenant, claim_id)


# ----------------------------------------------------------- notifications


@owner_notifications_router.get("", response_model=Page[NotificationResponse])
def owner_notifications(
    unread_only: bool = False, page: Pagination = Depends(),
    db: Session = Depends(get_db), owner: Owner = Depends(get_current_owner),
):
    items, total = notes.list_for(
        db, owner_id=owner.id, unread_only=unread_only, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@owner_notifications_router.post("/{notification_id}/read", response_model=NotificationResponse)
def owner_mark_read(
    notification_id: UUID, db: Session = Depends(get_db),
    owner: Owner = Depends(get_current_owner),
):
    return notes.mark_read(db, notification_id, owner_id=owner.id)


@tenant_notifications_router.get("", response_model=Page[NotificationResponse])
def tenant_notifications(
    unread_only: bool = False, page: Pagination = Depends(),
    db: Session = Depends(get_db), tenant: Tenant = Depends(get_current_tenant),
):
    items, total = notes.list_for(
        db, tenant_id=tenant.id, unread_only=unread_only, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@tenant_notifications_router.post("/{notification_id}/read", response_model=NotificationResponse)
def tenant_mark_read(
    notification_id: UUID, db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return notes.mark_read(db, notification_id, tenant_id=tenant.id)
