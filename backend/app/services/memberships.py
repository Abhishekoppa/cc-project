from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Membership, Tenant


def get_active_memberships(db: Session, subject: str) -> list[Membership]:
    """Resolve memberships only from trusted authenticated subject and active tenant state."""
    statement = (
        select(Membership)
        .join(Membership.tenant)
        .options(joinedload(Membership.tenant))
        .where(
            Membership.subject == subject,
            Membership.status == "active",
            Tenant.status == "active",
        )
        .order_by(Tenant.tenant_code)
    )
    return list(db.scalars(statement).all())
