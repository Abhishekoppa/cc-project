from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Faculty, Membership
from app.schemas import FacultyCreate, FacultyResponse, FacultyUpdate
from app.security.identity_records import current_faculty
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/faculty", tags=["faculty"])


@router.get("", response_model=list[FacultyResponse])
def list_faculty(
    department: str | None = Query(default=None),
    search: str | None = None,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> list[Faculty]:
    """Retrieve faculty members belonging ONLY to the active tenant."""
    query = select(Faculty).where(Faculty.tenant_id == ctx.tenant.id)
    if department:
        query = query.where(Faculty.department == department)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(
            Faculty.employee_id.ilike(pattern), Faculty.first_name.ilike(pattern),
            Faculty.last_name.ilike(pattern), Faculty.email.ilike(pattern),
        ))
    query = query.order_by(Faculty.employee_id)
    return list(db.scalars(query).all())


@router.get("/{faculty_id}", response_model=FacultyResponse)
def get_faculty(
    faculty_id: str,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
    db: Session = Depends(get_db),
) -> Faculty:
    """Retrieve a single faculty member within the active tenant."""
    faculty = db.scalar(
        select(Faculty).where(
            Faculty.id == faculty_id,
            Faculty.tenant_id == ctx.tenant.id,
        )
    )
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty member not found in this tenant")
    if ctx.role == "faculty" and current_faculty(ctx, db).id != faculty.id:
        raise HTTPException(status_code=404, detail="Faculty member not found in this tenant")
    return faculty


@router.post("", response_model=FacultyResponse, status_code=status.HTTP_201_CREATED)
def create_faculty(
    payload: FacultyCreate,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> Faculty:
    """Register a new faculty member into the active tenant."""
    existing = db.scalar(
        select(Faculty).where(
            Faculty.tenant_id == ctx.tenant.id,
            Faculty.employee_id == payload.employee_id,
        )
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Employee ID '{payload.employee_id}' already exists in this tenant",
        )
    if db.scalar(select(Faculty.id).where(Faculty.tenant_id == ctx.tenant.id, Faculty.email == str(payload.email))):
        raise HTTPException(status_code=409, detail="Email already exists in this tenant")

    if payload.auth_subject:
        membership = db.scalar(
            select(Membership).where(
                Membership.tenant_id == ctx.tenant.id,
                Membership.subject == payload.auth_subject,
                Membership.role == "faculty",
                Membership.status == "active",
            )
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="Active faculty account not found in this tenant")

    faculty = Faculty(
        tenant_id=ctx.tenant.id,
        employee_id=payload.employee_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=str(payload.email),
        phone=payload.phone,
        auth_subject=payload.auth_subject,
        department=payload.department,
        designation=payload.designation,
        status="active",
    )
    db.add(faculty)
    db.commit()
    db.refresh(faculty)
    return faculty


@router.patch("/{faculty_id}", response_model=FacultyResponse)
def update_faculty(faculty_id: str, payload: FacultyUpdate,
                   ctx: TenantContext = Depends(require_roles("college_admin")),
                   db: Session = Depends(get_db)) -> Faculty:
    faculty = db.scalar(select(Faculty).where(Faculty.id == faculty_id, Faculty.tenant_id == ctx.tenant.id))
    if faculty is None:
        raise HTTPException(status_code=404, detail="Faculty member not found in this tenant")
    changes = payload.model_dump(exclude_unset=True)
    employee_id = changes.get("employee_id")
    if employee_id and db.scalar(select(Faculty.id).where(
        Faculty.tenant_id == ctx.tenant.id, Faculty.employee_id == employee_id, Faculty.id != faculty.id
    )):
        raise HTTPException(status_code=409, detail="Employee ID already exists in this tenant")
    email = changes.get("email")
    if email and db.scalar(select(Faculty.id).where(
        Faculty.tenant_id == ctx.tenant.id, Faculty.email == email, Faculty.id != faculty.id
    )):
        raise HTTPException(status_code=409, detail="Email already exists in this tenant")
    for key, value in changes.items():
        setattr(faculty, key, value)
    db.commit()
    db.refresh(faculty)
    return faculty


@router.delete("/{faculty_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_faculty(faculty_id: str,
                   ctx: TenantContext = Depends(require_roles("college_admin")),
                   db: Session = Depends(get_db)) -> None:
    faculty = db.scalar(select(Faculty).where(Faculty.id == faculty_id, Faculty.tenant_id == ctx.tenant.id))
    if faculty is None:
        raise HTTPException(status_code=404, detail="Faculty member not found in this tenant")
    # Document bytes must be removed from S3 through the document endpoint first.
    from app.models import Document
    if db.scalar(select(Document.id).where(Document.tenant_id == ctx.tenant.id, Document.faculty_id == faculty.id)):
        raise HTTPException(status_code=409, detail="Remove this faculty member's documents before deletion")
    subjects = {faculty.email.strip().lower()}
    if faculty.auth_subject:
        subjects.add(faculty.auth_subject)
    db.execute(delete(Membership).where(
        Membership.tenant_id == ctx.tenant.id,
        Membership.role == "faculty",
        Membership.subject.in_(subjects),
    ))
    db.delete(faculty)
    db.commit()
