from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Attendance, Course, CourseAssignment, CourseEnrollment, Document, Examination, Faculty, Student
from app.schemas import CourseCreate, CourseFacultyAssignment, CourseResponse, CourseStudentEnrollment, CourseUpdate, StudentResponse
from app.security.identity_records import current_faculty, current_student
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("", response_model=list[CourseResponse])
def list_courses(
    department: str | None = Query(default=None),
    semester: int | None = Query(default=None),
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> list[CourseResponse]:
    """Retrieve courses belonging ONLY to the active tenant."""
    query = select(Course).where(Course.tenant_id == ctx.tenant.id, Course.status == "active")
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        query = query.join(CourseAssignment, CourseAssignment.course_id == Course.id).where(
            CourseAssignment.tenant_id == ctx.tenant.id,
            CourseAssignment.faculty_id == faculty.id,
        )
    elif ctx.role == "student":
        student = current_student(ctx, db)
        query = query.join(CourseEnrollment, CourseEnrollment.course_id == Course.id).where(
            CourseEnrollment.tenant_id == ctx.tenant.id,
            CourseEnrollment.student_id == student.id,
        )
    if department:
        query = query.where(Course.department == department)
    if semester is not None:
        query = query.where(Course.semester == semester)
    query = query.order_by(Course.course_code)
    courses = list(db.scalars(query).unique().all())
    response = []
    for course in courses:
        faculty_names = list(db.scalars(
            select(Faculty.first_name + " " + Faculty.last_name)
            .join(CourseAssignment, CourseAssignment.faculty_id == Faculty.id)
            .where(
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.course_id == course.id,
                Faculty.tenant_id == ctx.tenant.id,
                Faculty.status == "active",
            )
            .order_by(Faculty.last_name, Faculty.first_name)
        ).all())
        item = CourseResponse.model_validate(course)
        item.faculty_names = faculty_names
        response.append(item)
    return response


@router.post("", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseCreate,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> Course:
    """Create a new course catalog entry for the active tenant."""
    existing = db.scalar(
        select(Course).where(
            Course.tenant_id == ctx.tenant.id,
            Course.course_code == payload.course_code.upper(),
        )
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Course code '{payload.course_code}' already exists in this tenant",
        )

    course = Course(
        tenant_id=ctx.tenant.id,
        course_code=payload.course_code.upper(),
        title=payload.title,
        description=payload.description,
        credits=payload.credits,
        department=payload.department,
        semester=payload.semester,
        status="active",
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


@router.patch("/{course_id}", response_model=CourseResponse)
def update_course(course_id: str, payload: CourseUpdate,
                  ctx: TenantContext = Depends(require_roles("college_admin")),
                  db: Session = Depends(get_db)) -> Course:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.tenant_id == ctx.tenant.id))
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found in this tenant")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("course_code"):
        changes["course_code"] = changes["course_code"].upper()
        if db.scalar(select(Course.id).where(
            Course.tenant_id == ctx.tenant.id, Course.course_code == changes["course_code"], Course.id != course.id
        )):
            raise HTTPException(status_code=409, detail="Course code already exists in this tenant")
    for key, value in changes.items():
        setattr(course, key, value)
    db.commit()
    db.refresh(course)
    return course


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(course_id: str, ctx: TenantContext = Depends(require_roles("college_admin")),
                  db: Session = Depends(get_db)) -> None:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.tenant_id == ctx.tenant.id))
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found in this tenant")
    dependencies = (
        db.scalar(select(CourseEnrollment.id).where(CourseEnrollment.tenant_id == ctx.tenant.id, CourseEnrollment.course_id == course.id))
        or db.scalar(select(Attendance.id).where(Attendance.tenant_id == ctx.tenant.id, Attendance.course_id == course.id))
        or db.scalar(select(Examination.id).where(Examination.tenant_id == ctx.tenant.id, Examination.course_id == course.id))
        or db.scalar(select(Document.id).where(Document.tenant_id == ctx.tenant.id, Document.course_id == course.id))
    )
    if dependencies:
        raise HTTPException(status_code=409, detail="Course has academic records; archive it instead of deleting")
    db.delete(course)
    db.commit()


@router.post("/{course_id}/faculty", status_code=status.HTTP_201_CREATED)
def assign_faculty(
    course_id: str,
    payload: CourseFacultyAssignment,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.tenant_id == ctx.tenant.id))
    faculty = db.scalar(select(Faculty).where(Faculty.id == payload.faculty_id, Faculty.tenant_id == ctx.tenant.id))
    if course is None or faculty is None:
        raise HTTPException(status_code=404, detail="Course or faculty member not found in this tenant")
    assignment = db.scalar(
        select(CourseAssignment).where(
            CourseAssignment.tenant_id == ctx.tenant.id,
            CourseAssignment.course_id == course.id,
            CourseAssignment.faculty_id == faculty.id,
        )
    )
    if assignment is None:
        db.add(CourseAssignment(tenant_id=ctx.tenant.id, course_id=course.id, faculty_id=faculty.id))
        db.commit()
    return {"course_id": course.id, "faculty_id": faculty.id, "status": "assigned"}


@router.post("/{course_id}/students", status_code=status.HTTP_201_CREATED)
def enroll_student(
    course_id: str,
    payload: CourseStudentEnrollment,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.tenant_id == ctx.tenant.id))
    student = db.scalar(select(Student).where(Student.id == payload.student_id, Student.tenant_id == ctx.tenant.id))
    if course is None or student is None:
        raise HTTPException(status_code=404, detail="Course or student not found in this tenant")
    enrollment = db.scalar(
        select(CourseEnrollment).where(
            CourseEnrollment.tenant_id == ctx.tenant.id,
            CourseEnrollment.course_id == course.id,
            CourseEnrollment.student_id == student.id,
        )
    )
    if enrollment is None:
        db.add(CourseEnrollment(tenant_id=ctx.tenant.id, course_id=course.id, student_id=student.id))
        db.commit()
    return {"course_id": course.id, "student_id": student.id, "status": "enrolled"}


@router.get("/{course_id}/students", response_model=list[StudentResponse])
def list_course_students(course_id: str,
                         ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
                         db: Session = Depends(get_db)) -> list[Student]:
    course = db.scalar(select(Course).where(Course.id == course_id, Course.tenant_id == ctx.tenant.id))
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found in this tenant")
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        if db.scalar(select(CourseAssignment.id).where(
            CourseAssignment.tenant_id == ctx.tenant.id,
            CourseAssignment.course_id == course.id,
            CourseAssignment.faculty_id == faculty.id,
        )) is None:
            raise HTTPException(status_code=403, detail="Course is not assigned to this faculty member")
    return list(db.scalars(
        select(Student).join(CourseEnrollment, CourseEnrollment.student_id == Student.id).where(
            CourseEnrollment.tenant_id == ctx.tenant.id,
            CourseEnrollment.course_id == course.id,
            Student.tenant_id == ctx.tenant.id,
        ).order_by(Student.registration_number)
    ).all())


@router.delete("/{course_id}/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_enrollment(course_id: str, student_id: str,
                      ctx: TenantContext = Depends(require_roles("college_admin")),
                      db: Session = Depends(get_db)) -> None:
    enrollment = db.scalar(select(CourseEnrollment).where(
        CourseEnrollment.tenant_id == ctx.tenant.id,
        CourseEnrollment.course_id == course_id,
        CourseEnrollment.student_id == student_id,
    ))
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Course enrollment not found in this tenant")
    db.delete(enrollment)
    db.commit()
