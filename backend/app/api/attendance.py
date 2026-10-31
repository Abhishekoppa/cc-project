from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Attendance, Course, CourseAssignment, CourseEnrollment, Faculty, Student
from app.schemas import AttendanceBulkCreate, AttendanceResponse
from app.security.identity_records import current_faculty, current_student
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.get("/roster")
def attendance_roster(course_id: str = Query(...), attendance_date: date = Query(..., alias="date"),
                     ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
                     db: Session = Depends(get_db)) -> list[dict]:
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
    students = db.scalars(
        select(Student).join(CourseEnrollment, CourseEnrollment.student_id == Student.id).where(
            CourseEnrollment.tenant_id == ctx.tenant.id,
            CourseEnrollment.course_id == course.id,
            Student.tenant_id == ctx.tenant.id,
        ).order_by(Student.registration_number)
    ).all()
    attendance = {row.student_id: row for row in db.scalars(select(Attendance).where(
        Attendance.tenant_id == ctx.tenant.id,
        Attendance.course_id == course.id,
        Attendance.date == attendance_date,
    )).all()}
    return [{
        "student_id": student.id,
        "registration_number": student.registration_number,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "status": attendance[student.id].status if student.id in attendance else "present",
        "recorded": student.id in attendance,
    } for student in students]


@router.get("", response_model=list[AttendanceResponse])
def list_attendance(
    course_id: str | None = Query(default=None),
    attendance_date: date | None = Query(default=None, alias="date"),
    student_id: str | None = Query(default=None),
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> list[Attendance]:
    """Retrieve attendance logs for the active tenant."""
    query = select(Attendance).where(Attendance.tenant_id == ctx.tenant.id)
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        query = query.join(CourseAssignment, CourseAssignment.course_id == Attendance.course_id).where(
            CourseAssignment.tenant_id == ctx.tenant.id,
            CourseAssignment.faculty_id == faculty.id,
        )
        if course_id and db.scalar(
            select(CourseAssignment.id).where(
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.course_id == course_id,
                CourseAssignment.faculty_id == faculty.id,
            )
        ) is None:
            raise HTTPException(status_code=403, detail="Course is not assigned to this faculty member")
    elif ctx.role == "student":
        student = current_student(ctx, db)
        if student_id and student_id != student.id:
            raise HTTPException(status_code=404, detail="Attendance not found")
        query = query.join(
            CourseEnrollment,
            and_(
                CourseEnrollment.tenant_id == Attendance.tenant_id,
                CourseEnrollment.course_id == Attendance.course_id,
                CourseEnrollment.student_id == Attendance.student_id,
            ),
        ).where(Attendance.student_id == student.id)
    if course_id:
        query = query.where(Attendance.course_id == course_id)
    if attendance_date:
        query = query.where(Attendance.date == attendance_date)
    if student_id:
        query = query.where(Attendance.student_id == student_id)
    query = query.order_by(Attendance.date.desc())
    return list(db.scalars(query).unique().all())


@router.post("/bulk", response_model=list[AttendanceResponse], status_code=status.HTTP_201_CREATED)
def record_bulk_attendance(
    payload: AttendanceBulkCreate,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
    db: Session = Depends(get_db),
) -> list[Attendance]:
    """Batch-record attendance for multiple students in a course."""
    # Verify course belongs to tenant
    course = db.scalar(
        select(Course).where(Course.id == payload.course_id, Course.tenant_id == ctx.tenant.id)
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found in this tenant")

    actor_faculty = current_faculty(ctx, db) if ctx.role == "faculty" else None
    if actor_faculty and db.scalar(
        select(CourseAssignment.id).where(
            CourseAssignment.tenant_id == ctx.tenant.id,
            CourseAssignment.course_id == course.id,
            CourseAssignment.faculty_id == actor_faculty.id,
        )
    ) is None:
        raise HTTPException(status_code=403, detail="Course is not assigned to this faculty member")

    attendance_faculty_id = actor_faculty.id if actor_faculty else payload.faculty_id
    if payload.faculty_id and actor_faculty and payload.faculty_id != actor_faculty.id:
        raise HTTPException(status_code=403, detail="Faculty identity cannot be overridden")
    if attendance_faculty_id:
        faculty = db.scalar(
            select(Faculty).where(
                Faculty.id == attendance_faculty_id,
                Faculty.tenant_id == ctx.tenant.id,
            )
        )
        if faculty is None:
            raise HTTPException(status_code=404, detail="Faculty member not found in this tenant")
        if db.scalar(
            select(CourseAssignment.id).where(
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.course_id == course.id,
                CourseAssignment.faculty_id == faculty.id,
            )
        ) is None:
            raise HTTPException(status_code=403, detail="Faculty member is not assigned to this course")

    created_records: list[Attendance] = []
    seen_students: set[str] = set()
    for item in payload.records:
        student_id = item.student_id
        if student_id in seen_students:
            raise HTTPException(status_code=422, detail="A student may appear only once per attendance request")
        seen_students.add(student_id)
        record_status = item.status

        student = db.scalar(
            select(Student).where(Student.id == student_id, Student.tenant_id == ctx.tenant.id)
        )
        if student is None:
            raise HTTPException(status_code=404, detail="Student not found in this tenant")
        enrolled = db.scalar(
            select(CourseEnrollment.id).where(
                CourseEnrollment.tenant_id == ctx.tenant.id,
                CourseEnrollment.course_id == course.id,
                CourseEnrollment.student_id == student.id,
            )
        )
        if enrolled is None:
            raise HTTPException(status_code=422, detail="Student is not enrolled in this course")

        # Check existing record
        existing = db.scalar(
            select(Attendance).where(
                Attendance.tenant_id == ctx.tenant.id,
                Attendance.student_id == student_id,
                Attendance.course_id == payload.course_id,
                Attendance.date == payload.date,
            )
        )
        if existing:
            existing.status = record_status
            existing.faculty_id = attendance_faculty_id
            created_records.append(existing)
        else:
            rec = Attendance(
                tenant_id=ctx.tenant.id,
                student_id=student_id,
                course_id=payload.course_id,
                faculty_id=attendance_faculty_id,
                date=payload.date,
                status=record_status,
            )
            db.add(rec)
            created_records.append(rec)

    db.commit()
    for r in created_records:
        db.refresh(r)
    return created_records
