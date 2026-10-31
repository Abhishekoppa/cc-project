from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Faculty, LocalAccount, Student
from app.security.tenant_context import TenantContext


def _identity_email(ctx: TenantContext, db: Session) -> str | None:
    subject = ctx.principal.subject
    if subject.startswith("local:"):
        account = db.scalar(
            select(LocalAccount).where(
                LocalAccount.id == subject.removeprefix("local:"),
                LocalAccount.tenant_id == ctx.tenant.id,
            )
        )
        return account.email if account else None
    if "@" in subject:
        return subject.strip().lower()
    email = ctx.principal.claims.get("email")
    if isinstance(email, str) and ctx.principal.claims.get("email_verified") is True:
        return email.strip().lower()
    return None


def current_student(ctx: TenantContext, db: Session) -> Student:
    subject_match = db.scalar(
        select(Student).where(
            Student.tenant_id == ctx.tenant.id,
            Student.auth_subject == ctx.principal.subject,
        )
    )
    if subject_match:
        return subject_match
    email = _identity_email(ctx, db)
    if email:
        return db.scalar(
            select(Student).where(Student.tenant_id == ctx.tenant.id, Student.email == email)
        ) or _not_found("Student profile not found")
    raise HTTPException(status_code=403, detail="No student profile is linked to this account")


def current_faculty(ctx: TenantContext, db: Session) -> Faculty:
    subject_match = db.scalar(
        select(Faculty).where(
            Faculty.tenant_id == ctx.tenant.id,
            Faculty.auth_subject == ctx.principal.subject,
        )
    )
    if subject_match:
        return subject_match
    email = _identity_email(ctx, db)
    if email:
        return db.scalar(
            select(Faculty).where(Faculty.tenant_id == ctx.tenant.id, Faculty.email == email)
        ) or _not_found("Faculty profile not found")
    raise HTTPException(status_code=403, detail="No faculty profile is linked to this account")


def _not_found(message: str):
    raise HTTPException(status_code=404, detail=message)
