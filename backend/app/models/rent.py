import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

PAYMENT_METHODS = ("cash", "upi", "bank_transfer", "card", "cheque", "other")
_METHODS_SQL = ", ".join(f"'{m}'" for m in PAYMENT_METHODS)


class RentObligation(Base):
    """Rent owed for one period of a stay.

    ``amount_paid`` counts VERIFIED payments only. It is changed solely
    under a row lock together with a RentPayment / reversal row, and the
    check constraint makes overpayment impossible even if a code path
    forgets the lock. Claims never touch it.
    """

    __tablename__ = "rent_obligations"

    __table_args__ = (
        UniqueConstraint("stay_id", "period_start", name="uq_rent_obligations_stay_period"),
        CheckConstraint("amount_due > 0", name="ck_rent_obligations_amount_due_positive"),
        CheckConstraint(
            "amount_adjusted >= 0 AND amount_adjusted <= amount_due",
            name="ck_rent_obligations_amount_adjusted_range",
        ),
        # Verified payments can never exceed the EFFECTIVE amount due
        # (amount_due - amount_adjusted): no overpayment, no unsupported credit.
        CheckConstraint(
            "amount_paid >= 0 AND amount_paid + amount_adjusted <= amount_due",
            name="ck_rent_obligations_amount_paid_range",
        ),
        CheckConstraint("period_end > period_start", name="ck_rent_obligations_period"),
        Index("ix_rent_obligations_property_due", "property_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stay_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stays.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    # Sum of active (non-reversed) waivers/reductions. amount_due stays the
    # ORIGINAL amount; effective due = amount_due - amount_adjusted.
    amount_adjusted: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00"), server_default=text("0")
    )
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00"), server_default=text("0")
    )
    created_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PaymentClaim(Base):
    """A tenant's claim that they paid. A claim is NOT a payment."""

    __tablename__ = "payment_claims"

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_claims_amount_positive"),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')", name="ck_payment_claims_status"
        ),
        CheckConstraint(f"method IN ({_METHODS_SQL})", name="ck_payment_claims_method"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) AND "
            "(status = 'pending') = (decided_by_owner_id IS NULL)",
            name="ck_payment_claims_decision_consistency",
        ),
        Index("ix_payment_claims_property_status", "property_id", "status", "created_at"),
        # At most one pending claim per tenant and obligation, even under
        # concurrent submissions. Rejected claims stay in history.
        Index(
            "uq_payment_claims_one_pending",
            "obligation_id",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rent_obligations.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RentPayment(Base):
    """Verified, append-only ledger entry. Only these count as paid."""

    __tablename__ = "rent_payments"

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_rent_payments_amount_positive"),
        CheckConstraint(f"method IN ({_METHODS_SQL})", name="ck_rent_payments_method"),
        CheckConstraint(
            "source IN ('claim', 'owner_recorded')", name="ck_rent_payments_source"
        ),
        CheckConstraint(
            "(source = 'claim') = (claim_id IS NOT NULL)", name="ck_rent_payments_claim_source"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rent_obligations.id"), nullable=False, index=True
    )
    # A claim can yield at most one payment: duplicate confirmation is
    # impossible at the database level too.
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_claims.id"), nullable=True, unique=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recorded_by_owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RentPaymentReversal(Base):
    """Audited reversal of a verified payment. A payment is reversed once."""

    __tablename__ = "rent_payment_reversals"

    __table_args__ = (
        CheckConstraint("length(trim(reason)) > 0", name="ck_rent_payment_reversals_reason"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rent_payments.id"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reversed_by_owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RentAdjustment(Base):
    """Audited reduction (waiver) of an obligation. Append-only; the
    original amount_due is never edited."""

    __tablename__ = "rent_adjustments"

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_rent_adjustments_amount_positive"),
        CheckConstraint("length(trim(reason)) > 0", name="ck_rent_adjustments_reason"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rent_obligations.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RentAdjustmentReversal(Base):
    """Audited undo of an adjustment. An adjustment is reversed once."""

    __tablename__ = "rent_adjustment_reversals"

    __table_args__ = (
        CheckConstraint("length(trim(reason)) > 0", name="ck_rent_adjustment_reversals_reason"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    adjustment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rent_adjustments.id"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reversed_by_owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
