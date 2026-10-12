"""Reviews & ratings.

Eligibility
* A tenant may review a stay only if it has an ACTUAL check-in
  (``Stay.actual_check_in_at`` set, status checked_in or checked_out).
  Reserved, cancelled and no-show stays, and booking requests, never
  qualify.
* One review per stay: ``reviews.stay_id`` is unique. Deleting a review
  frees that stay to be reviewed again; a later, separate stay is always
  a different ``stay_id``.

Authorization (always enforced in the queries, never from client input)
* The tenant is the authenticated tenant: every tenant query filters on
  ``Stay.tenant_id``. Someone else's review or stay looks nonexistent (404).
* An owner reaches a review only through ``Property.owner_id``.
* Owners write only the response columns; rating and comment are tenant-only.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.owner import Owner
from app.models.property import Property
from app.models.review import Review
from app.models.stay import Stay
from app.models.tenant import Tenant
from app.schemas.review import RatingSummary, ReviewCreate, ReviewUpdate
from app.services import stay_rules as rules
from app.services.access import (
    require_manageable_property,
    require_owned_property_for_history,
)
from app.services.errors import ConflictError, NotFoundError

DUPLICATE_MESSAGE = "You have already reviewed this stay."
NOT_STAYED_MESSAGE = (
    "You can only review a stay after you have actually checked in; "
    "a reservation or booking request is not a stay."
)


def _rows():
    """Review + its stay's property/tenant, one row per review (all joins
    are many-to-one)."""
    return (
        select(Review, Stay, Property.name, Tenant.full_name)
        .join(Stay, Stay.id == Review.stay_id)
        .join(Property, Property.id == Stay.property_id)
        .join(Tenant, Tenant.id == Stay.tenant_id)
    )


def _one(db: Session, *filters, lock: bool = False):
    stmt = _rows().where(*filters)
    if lock:
        stmt = stmt.with_for_update(of=Review)
    row = db.execute(stmt).first()
    if row is None:
        raise NotFoundError("Review not found.")
    return tuple(row)


def stay_dates(stay: Stay) -> tuple[date | None, date | None]:
    def conv(moment):
        return rules.business_date(moment) if moment is not None else None

    return conv(stay.actual_check_in_at), conv(stay.actual_check_out_at)


# ------------------------------------------------------------------ tenant


def create_review(db: Session, tenant: Tenant, data: ReviewCreate):
    try:
        # The stay must belong to the authenticated tenant.
        stay = db.scalar(
            select(Stay).where(Stay.id == data.stay_id, Stay.tenant_id == tenant.id)
        )
        if stay is None:
            raise NotFoundError("Stay not found.")
        if stay.actual_check_in_at is None:
            raise ConflictError(NOT_STAYED_MESSAGE)
        if db.scalar(select(Review.id).where(Review.stay_id == stay.id)):
            raise ConflictError(DUPLICATE_MESSAGE)
        review = Review(
            id=uuid4(), stay_id=stay.id, rating=data.rating, comment=data.comment
        )
        db.add(review)
        db.commit()
    except IntegrityError as exc:  # concurrent duplicate: the unique index decides
        db.rollback()
        raise ConflictError(DUPLICATE_MESSAGE) from exc
    except Exception:
        db.rollback()
        raise
    return _one(db, Review.id == review.id)


def list_for_tenant(
    db: Session, tenant: Tenant, property_id: UUID | None, limit: int, offset: int
):
    filters = [Stay.tenant_id == tenant.id]
    if property_id is not None:
        filters.append(Stay.property_id == property_id)
    return _page(db, filters, limit, offset)


def get_for_tenant(db: Session, tenant: Tenant, review_id: UUID):
    return _one(db, Review.id == review_id, Stay.tenant_id == tenant.id)


def update_review(db: Session, tenant: Tenant, review_id: UUID, data: ReviewUpdate):
    try:
        review = _one(db, Review.id == review_id, Stay.tenant_id == tenant.id, lock=True)[0]
        changed = False
        if data.rating is not None and data.rating != review.rating:
            review.rating, changed = data.rating, True
        if data.comment is not None and data.comment != review.comment:
            review.comment, changed = data.comment, True
        if changed:
            review.edited_at = rules.utc_now()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _one(db, Review.id == review_id)


def delete_review(db: Session, tenant: Tenant, review_id: UUID) -> None:
    try:
        review = _one(db, Review.id == review_id, Stay.tenant_id == tenant.id, lock=True)[0]
        db.delete(review)
        db.commit()
    except Exception:
        db.rollback()
        raise


# ------------------------------------------------------------------- owner


def _page(db: Session, filters: list, limit: int, offset: int):
    total = db.scalar(
        select(func.count(Review.id))
        .select_from(Review)
        .join(Stay, Stay.id == Review.stay_id)
        .join(Property, Property.id == Stay.property_id)
        .where(*filters)
    )
    rows = db.execute(
        _rows().where(*filters)
        .order_by(Review.created_at.desc(), Review.id)
        .limit(limit).offset(offset)
    ).all()
    return [tuple(r) for r in rows], total or 0


def list_for_owner(
    db: Session, owner: Owner, property_id: UUID | None, rating: int | None,
    has_response: bool | None, limit: int, offset: int,
):
    filters = [Property.owner_id == owner.id]
    if property_id is not None:
        require_owned_property_for_history(db, owner, property_id)
        filters.append(Stay.property_id == property_id)
    if rating is not None:
        filters.append(Review.rating == rating)
    if has_response is not None:
        filters.append(
            Review.owner_response.is_not(None) if has_response
            else Review.owner_response.is_(None)
        )
    return _page(db, filters, limit, offset)


def get_for_owner(db: Session, owner: Owner, property_id: UUID, review_id: UUID):
    prop = require_owned_property_for_history(db, owner, property_id)
    return _one(
        db, Review.id == review_id, Stay.property_id == prop.id, Property.owner_id == owner.id
    )


def respond(
    db: Session, owner: Owner, property_id: UUID, review_id: UUID, response: str | None
):
    """Set (or, with None, remove) the owner's response. Nothing else on
    the review is touched."""
    prop = require_manageable_property(db, owner, property_id)
    try:
        review = _one(
            db, Review.id == review_id, Stay.property_id == prop.id,
            Property.owner_id == owner.id, lock=True,
        )[0]
        if response is None:
            if review.owner_response is None:
                raise NotFoundError("This review has no response.")
            review.owner_response = None
            review.owner_responded_at = None
            review.owner_responded_by_owner_id = None
        else:
            review.owner_response = response
            review.owner_responded_at = rules.utc_now()
            review.owner_responded_by_owner_id = owner.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _one(db, Review.id == review_id)


def summary(db: Session, owner: Owner, property_id: UUID) -> RatingSummary:
    prop = require_owned_property_for_history(db, owner, property_id)
    columns = [
        func.count(Review.id),
        func.avg(Review.rating),
        func.count(Review.id).filter(Review.owner_response.is_not(None)),
    ] + [func.count(Review.id).filter(Review.rating == star) for star in range(1, 6)]
    row = db.execute(
        select(*columns)
        .select_from(Review)
        .join(Stay, Stay.id == Review.stay_id)
        .join(Property, Property.id == Stay.property_id)
        .where(Stay.property_id == prop.id, Property.owner_id == owner.id)
    ).one()
    total = row[0]
    return RatingSummary(
        property_id=prop.id,
        total_reviews=total,
        average_rating=(Decimal(row[1]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if total else None),
        rating_counts={str(star): row[3 + star - 1] for star in range(1, 6)},
        responded_reviews=row[2],
    )
