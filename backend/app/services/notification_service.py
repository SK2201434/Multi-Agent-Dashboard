"""In-app notifications. Always written inside the caller's transaction."""

from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.services import stay_rules as rules
from app.services.errors import NotFoundError


def notify(
    db: Session,
    *,
    owner_id: UUID | None = None,
    tenant_id: UUID | None = None,
    kind: str,
    title: str,
    body: str,
    dedupe_key: str,
    obligation_id: UUID | None = None,
    claim_id: UUID | None = None,
) -> bool:
    """Insert unless this recipient already has dedupe_key. True if created."""
    assert (owner_id is None) != (tenant_id is None), "exactly one recipient"
    result = db.execute(
        insert(Notification)
        .values(
            id=uuid4(),
            owner_id=owner_id,
            tenant_id=tenant_id,
            kind=kind,
            title=title,
            body=body,
            dedupe_key=dedupe_key,
            obligation_id=obligation_id,
            claim_id=claim_id,
        )
        .on_conflict_do_nothing()
        .returning(Notification.id)
    )
    return result.first() is not None


def _recipient_filter(owner_id: UUID | None, tenant_id: UUID | None):
    return (
        Notification.owner_id == owner_id
        if owner_id is not None
        else Notification.tenant_id == tenant_id
    )


def list_for(
    db: Session,
    *,
    owner_id: UUID | None = None,
    tenant_id: UUID | None = None,
    unread_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[Notification], int]:
    filters = [_recipient_filter(owner_id, tenant_id)]
    if unread_only:
        filters.append(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(Notification).where(*filters))
    items = db.scalars(
        select(Notification)
        .where(*filters)
        .order_by(Notification.created_at.desc(), Notification.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), total or 0


def mark_read(
    db: Session,
    notification_id: UUID,
    *,
    owner_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> Notification:
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            _recipient_filter(owner_id, tenant_id),
        )
    )
    if notification is None:
        raise NotFoundError("Notification not found.")
    if notification.read_at is None:
        notification.read_at = rules.utc_now()
        db.commit()
    return notification
