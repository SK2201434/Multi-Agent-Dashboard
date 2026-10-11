from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.stay import Page, UtcDatetime, _StrictRequest  # noqa: F401

Money = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
PaymentMethod = Literal["cash", "upi", "bank_transfer", "card", "cheque", "other"]
ClaimStatus = Literal["pending", "confirmed", "rejected"]
SettlementStatus = Literal["unpaid", "partially_paid", "paid", "overdue", "waived"]


# ------------------------------------------------------------- requests


class ObligationCreate(_StrictRequest):
    period_start: date
    due_date: date
    # Defaults to the stay's agreed rent snapshot; an explicit amount is the
    # owner's own decision (e.g. a prorated first month).
    amount: Money | None = None


class PaymentClaimCreate(_StrictRequest):
    amount: Money
    method: PaymentMethod
    paid_on: date
    reference: str | None = Field(default=None, max_length=100)


class OwnerPaymentCreate(_StrictRequest):
    amount: Money
    method: PaymentMethod
    paid_on: date
    reference: str | None = Field(default=None, max_length=100)


class ClaimRejection(_StrictRequest):
    reason: str | None = Field(default=None, max_length=500)


class ReversalRequest(_StrictRequest):
    reason: str = Field(min_length=3, max_length=500)


class AdjustmentCreate(_StrictRequest):
    """Reduce (waive) part or all of the outstanding rent."""

    amount: Money
    reason: str = Field(min_length=3, max_length=500)


# ------------------------------------------------------------ responses


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    obligation_id: UUID
    claim_id: UUID | None
    source: str
    amount: Decimal
    method: str
    paid_on: date
    reference: str | None
    created_at: UtcDatetime
    reversed: bool = False
    reversal_reason: str | None = None


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    obligation_id: UUID
    tenant_id: UUID
    amount: Decimal
    method: str
    paid_on: date
    reference: str | None
    status: ClaimStatus
    decided_at: UtcDatetime | None
    rejection_reason: str | None
    created_at: UtcDatetime


class OwnerClaimResponse(ClaimResponse):
    property_id: UUID
    decided_by_owner_id: UUID | None
    tenant_full_name: str


class AdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    obligation_id: UUID
    amount: Decimal
    reason: str
    created_by_owner_id: UUID | None = None
    created_at: UtcDatetime
    reversed: bool = False
    reversal_reason: str | None = None


class ObligationResponse(BaseModel):
    id: UUID
    stay_id: UUID
    tenant_id: UUID
    property_id: UUID
    period_start: date
    period_end: date
    due_date: date
    # The ORIGINAL amount; never edited. Waivers are listed as adjustments.
    amount_due: Decimal
    amount_adjusted: Decimal
    effective_amount_due: Decimal
    # Verified payments only. Pending/rejected claims never count here.
    amount_paid: Decimal
    # effective_amount_due - amount_paid
    outstanding: Decimal
    payment_status: SettlementStatus
    created_at: UtcDatetime


class OwnerObligationResponse(ObligationResponse):
    tenant_full_name: str


class ObligationDetailResponse(ObligationResponse):
    payments: list[PaymentResponse] = []
    claims: list[ClaimResponse] = []
    adjustments: list[AdjustmentResponse] = []


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    title: str
    body: str
    obligation_id: UUID | None
    claim_id: UUID | None
    read_at: UtcDatetime | None
    created_at: UtcDatetime
