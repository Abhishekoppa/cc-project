from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CourseAssignment, CourseEnrollment, Document, Student
from app.schemas import StudentCreate, StudentResponse, StudentUpdate
from app.security.identity_records import current_faculty, current_student
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/students", tags=["students"])


@router.get("", response_model=list[StudentResponse])
def list_students(
    department: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
    db: Session = Depends(get_db),
) -> list[Student]:
    """Retrieve students belonging ONLY to the active tenant."""
    query = select(Student).where(Student.tenant_id == ctx.tenant.id)
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        query = (
            query.join(CourseEnrollment, CourseEnrollment.student_id == Student.id)
            .join(CourseAssignment, CourseAssignment.course_id == CourseEnrollment.course_id)
            .where(
                CourseEnrollment.tenant_id == ctx.tenant.id,
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.faculty_id == faculty.id,
            )
            .distinct()
        )
    if department:
        query = query.where(Student.department == department)
    if status_filter:
        query = query.where(Student.status == status_filter)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(
            Student.registration_number.ilike(pattern), Student.first_name.ilike(pattern),
            Student.last_name.ilike(pattern), Student.email.ilike(pattern),
        ))
    query = query.order_by(Student.registration_number)
    return list(db.scalars(query).all())


@router.get("/me", response_model=StudentResponse)
def get_my_student_record(
    ctx: TenantContext = Depends(require_roles("student")),
    db: Session = Depends(get_db),
) -> Student:
    return current_student(ctx, db)


@router.get("/{student_id}", response_model=StudentResponse)
def get_student(
    student_id: str,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> Student:
    """Retrieve a single student within the active tenant."""
    student = db.scalar(
        select(Student).where(
            Student.id == student_id,
            Student.tenant_id == ctx.tenant.id,
        )
    )
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this tenant")
    if ctx.role == "student" and current_student(ctx, db).id != student.id:
        raise HTTPException(status_code=404, detail="Student not found in this tenant")
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        assigned = db.scalar(
            select(Student.id)
            .join(CourseEnrollment, CourseEnrollment.student_id == Student.id)
            .join(CourseAssignment, CourseAssignment.course_id == CourseEnrollment.course_id)
            .where(
                Student.id == student.id,
                CourseEnrollment.tenant_id == ctx.tenant.id,
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.faculty_id == faculty.id,
            )
        )
        if assigned is None:
            raise HTTPException(status_code=404, detail="Student not found in your assigned courses")
    return student


@router.post("", response_model=StudentResponse, status_code=status.HTTP_201_CREATED)
def create_student(
    payload: StudentCreate,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> Student:
    """Enroll a new student into the active tenant."""
    existing = db.scalar(
        select(Student).where(
            Student.tenant_id == ctx.tenant.id,
            Student.registration_number == payload.registration_number,
        )
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Registration number '{payload.registration_number}' already exists in this tenant",
        )
    if db.scalar(select(Student.id).where(Student.tenant_id == ctx.tenant.id, Student.email == str(payload.email))):
        raise HTTPException(status_code=409, detail="Email already exists in this tenant")

    student = Student(
        tenant_id=ctx.tenant.id,
        registration_number=payload.registration_number,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=str(payload.email),
        phone=payload.phone,
        date_of_birth=payload.date_of_birth,
        enrollment_date=payload.enrollment_date,
        department=payload.department,
        semester=payload.semester,
        status="active",
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


@router.delete("/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(
    student_id: str,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> None:
    """Delete a student record scoped strictly to the current tenant."""
    student = db.scalar(
        select(Student).where(
            Student.id == student_id,
            Student.tenant_id == ctx.tenant.id,
        )
    )
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this tenant")
    if db.scalar(select(Document.id).where(Document.tenant_id == ctx.tenant.id, Document.student_id == student.id)):
        raise HTTPException(status_code=409, detail="Remove this student's documents before deletion")
    db.delete(student)
    db.commit()


@router.patch("/{student_id}", response_model=StudentResponse)
def update_student(student_id: str, payload: StudentUpdate,
                   ctx: TenantContext = Depends(require_roles("college_admin")),
                   db: Session = Depends(get_db)) -> Student:
    student = db.scalar(select(Student).where(Student.id == student_id, Student.tenant_id == ctx.tenant.id))
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found in this tenant")
    changes = payload.model_dump(exclude_unset=True)
    registration_number = changes.get("registration_number")
    if registration_number and db.scalar(select(Student.id).where(
        Student.tenant_id == ctx.tenant.id, Student.registration_number == registration_number, Student.id != student.id
    )):
        raise HTTPException(status_code=409, detail="Registration number already exists in this tenant")
    email = changes.get("email")
    if email and db.scalar(select(Student.id).where(
        Student.tenant_id == ctx.tenant.id, Student.email == email, Student.id != student.id
    )):
        raise HTTPException(status_code=409, detail="Email already exists in this tenant")
    for key, value in changes.items():
        setattr(student, key, value)
    db.commit()
    db.refresh(student)
    return student
