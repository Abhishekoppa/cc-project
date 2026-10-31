from dataclasses import dataclass
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Membership, Tenant
from app.security.cognito import CurrentPrincipal, get_current_principal


@dataclass(frozen=True)
class TenantContext:
    tenant: Tenant
    role: str
    principal: CurrentPrincipal


def get_current_tenant_context(
    x_tenant_code: str | None = Header(default=None, alias="X-Tenant-Code"),
    tenant_code_param: str | None = Query(default=None, alias="tenant"),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> TenantContext:
    """Validate that the authenticated principal has active membership in the target tenant.

    Resolves tenant by header 'X-Tenant-Code' or query parameter '?tenant=VIT001'.
    If neither is supplied, defaults to the principal's first active tenant membership.
    """
    requested_code = x_tenant_code or tenant_code_param

    # Query all active memberships for this authenticated subject
    statement = (
        select(Membership, Tenant)
        .join(Tenant, Membership.tenant_id == Tenant.id)
        .where(
            Membership.subject == principal.subject,
            Membership.status == "active",
            Tenant.status == "active",
        )
    )

    memberships = db.execute(statement).all()

    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no active tenant memberships",
        )

    # Match requested tenant
    if requested_code:
        for m, t in memberships:
            if t.tenant_code.upper() == requested_code.upper() or t.id == requested_code:
                return TenantContext(tenant=t, role=m.role, principal=principal)
        
        # If user is platform admin, they can access any active tenant
        is_admin = any(m.role == "platform_admin" for m, _ in memberships)
        if is_admin:
            target_tenant = db.execute(
                select(Tenant).where(
                    (Tenant.tenant_code == requested_code.upper()) | (Tenant.id == requested_code),
                    Tenant.status == "active",
                )
            ).scalar_one_or_none()
            if target_tenant:
                return TenantContext(tenant=target_tenant, role="platform_admin", principal=principal)

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to tenant '{requested_code}'",
        )

    # Default to first active membership
    if memberships:
        m, t = memberships[0]
        return TenantContext(tenant=t, role=m.role, principal=principal)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No active tenant found",
    )


def require_platform_admin(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CurrentPrincipal:
    """Require an explicitly authorized platform administrator."""
    membership = db.scalar(
        select(Membership)
        .join(Tenant, Membership.tenant_id == Tenant.id)
        .where(
            Membership.subject == principal.subject,
            Membership.role == "platform_admin",
            Membership.status == "active",
            Tenant.status == "active",
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform administrator access required")
    return principal


def require_roles(*allowed_roles: str):
    """Build a route dependency that authorizes using the database membership role."""
    allowed = frozenset(allowed_roles)

    def dependency(ctx: TenantContext = Depends(get_current_tenant_context)) -> TenantContext:
        if ctx.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This operation is not permitted for your role")
        return ctx

    return dependency
