from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Tenant
from app.schemas import TenantCreate, TenantResponse, TenantUpdate
from app.security.cognito import CurrentPrincipal
from app.security.tenant_context import require_platform_admin

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("/public", response_model=list[dict[str, str]], include_in_schema=True)
def list_public_tenants(db: Session = Depends(get_db)) -> list[dict[str, str]]:
    """Return the minimal active campus directory used before sign-in."""
    tenants = db.scalars(
        select(Tenant).where(Tenant.status == "active").order_by(Tenant.tenant_code)
    ).all()
    return [
        {"tenant_code": tenant.tenant_code, "name": tenant.name, "slug": tenant.slug}
        for tenant in tenants
    ]


@router.get("", response_model=list[TenantResponse])
def list_tenants(
    db: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_platform_admin),
) -> list[Tenant]:
    """List all available college tenants."""
    return list(db.scalars(select(Tenant).where(Tenant.status == "active").order_by(Tenant.tenant_code)).all())


@router.get("/{tenant_code}", response_model=TenantResponse)
def get_tenant(
    tenant_code: str,
    db: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_platform_admin),
) -> Tenant:
    """Retrieve details for a specific tenant code."""
    tenant = db.scalar(
        select(Tenant).where(
            (Tenant.tenant_code == tenant_code.upper()) | (Tenant.slug == tenant_code.lower())
        )
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_platform_admin),
) -> Tenant:
    """Register a new institution tenant."""
    existing = db.scalar(
        select(Tenant).where(
            (Tenant.tenant_code == payload.tenant_code.upper()) | (Tenant.slug == payload.slug.lower())
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Tenant code or slug already exists")

    tenant = Tenant(
        tenant_code=payload.tenant_code.upper(),
        name=payload.name,
        slug=payload.slug.lower(),
        status="active",
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.patch("/{tenant_code}", response_model=TenantResponse)
def update_tenant(
    tenant_code: str,
    payload: TenantUpdate,
    db: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_platform_admin),
) -> Tenant:
    tenant = db.scalar(
        select(Tenant).where(
            (Tenant.tenant_code == tenant_code.upper()) | (Tenant.slug == tenant_code.lower())
        )
    )
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    new_slug = payload.slug.lower().strip() if payload.slug is not None else None
    if new_slug and db.scalar(select(Tenant.id).where(Tenant.slug == new_slug, Tenant.id != tenant.id)):
        raise HTTPException(status_code=409, detail="Tenant slug already exists")
    if payload.name is not None:
        tenant.name = payload.name.strip()
    if new_slug:
        tenant.slug = new_slug
    if payload.status is not None:
        tenant.status = payload.status
    db.commit()
    db.refresh(tenant)
    return tenant
