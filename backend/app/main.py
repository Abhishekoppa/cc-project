from fastapi import FastAPI, Depends, HTTPException
from botocore.config import Config as BotoConfig
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
import json
import os
from pathlib import Path
from urllib.request import urlopen

from app.api.attendance import router as attendance_router
from app.api.auth import router as auth_router
from app.api.courses import router as courses_router
from app.api.examinations import router as examinations_router
from app.api.faculty import router as faculty_router
from app.api.fees import router as fees_router
from app.api.imports import router as imports_router
from app.api.reports import router as reports_router
from app.api.students import router as students_router
from app.api.storage import router as storage_router
from app.api.tenants import router as tenants_router
from app.core.config import settings
from app.db import get_db
from app.security.cognito import CurrentPrincipal, get_current_principal
from app.services.memberships import get_active_memberships

app = FastAPI(
    title="CampusCloud Multi-Tenant SaaS API",
    description="Multi-Tenant College Management System backend with strict per-tenant data isolation and AWS integration.",
    version=settings.app_version,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-Code"],
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc
    return {"status": "ready"}


@app.get("/info", tags=["health"])
@app.get("/api/v1/info", tags=["health"])
def deployment_info(db: Session = Depends(get_db)) -> dict:
    """Safe runtime facts for the authenticated frontend cloud status view."""
    import boto3

    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unavailable"
    bind = db.get_bind()
    db_type = "PostgreSQL" if bind.dialect.name.startswith("postgresql") else bind.dialect.name
    db_host = make_url(settings.resolved_database_url).host or "localhost"
    is_rds_target = db_host.lower().endswith(".rds.amazonaws.com")
    storage_status = "not_configured"
    storage_configured = bool(settings.s3_bucket and settings.aws_region)
    if storage_configured:
        try:
            boto = boto3.client(
                "s3", region_name=settings.aws_region,
                config=BotoConfig(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1}),
            )
            boto.head_bucket(Bucket=settings.s3_bucket)
            storage_status = "connected"
        except Exception:
            storage_status = "unavailable"
    metadata_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4") or os.environ.get("ECS_CONTAINER_METADATA_URI")
    if metadata_uri:
        try:
            with urlopen(f"{metadata_uri.rstrip('/')}/task", timeout=1) as response:
                metadata = json.load(response)
            runtime = "Amazon ECS Fargate" if metadata.get("LaunchType") == "FARGATE" else "Amazon ECS"
        except Exception:
            runtime = "Container (ECS runtime unverified)"
    elif Path("/.dockerenv").exists():
        runtime = "Docker container"
    else:
        runtime = "Local process"
    return {
        "service": "CampusCloud API", "version": settings.app_version,
        "environment": settings.app_env, "runtime": runtime,
        "aws_region": settings.aws_region or "not_configured",
        "region_configured": bool(settings.aws_region),
        "database_type": db_type,
        "database_target": (
            "RDS hostname configured; AWS service identity not verified"
            if is_rds_target else
            "Local database" if db_host in {"localhost", "127.0.0.1", "db"} else "Remote database"
        ),
        "database_connectivity": db_status,
        "s3_configured": storage_configured,
        "s3_connectivity": storage_status,
    }


@app.get("/api/v1/health", tags=["health"])
def api_health() -> dict[str, str]:
    return {"status": "ok", "service": "campuscloud-api"}


@app.get("/api/v1/me", tags=["profile"])
def me(principal: CurrentPrincipal = Depends(get_current_principal)) -> dict[str, str]:
    return {"subject": principal.subject}


@app.get("/api/v1/me/memberships", tags=["profile"])
def my_memberships(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[dict[str, str]]:
    memberships = get_active_memberships(db, principal.subject)
    return [
        {
            "tenant_id": membership.tenant_id,
            "tenant_code": membership.tenant.tenant_code,
            "tenant_name": membership.tenant.name,
            "role": membership.role,
        }
        for membership in memberships
    ]


# Mount College Management Subsystems
app.include_router(tenants_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(students_router, prefix="/api/v1")
app.include_router(faculty_router, prefix="/api/v1")
app.include_router(courses_router, prefix="/api/v1")
app.include_router(attendance_router, prefix="/api/v1")
app.include_router(examinations_router, prefix="/api/v1")
app.include_router(fees_router, prefix="/api/v1")
app.include_router(imports_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(storage_router, prefix="/api/v1")
