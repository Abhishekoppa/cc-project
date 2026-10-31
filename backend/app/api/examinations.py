from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import Course, CourseAssignment, CourseEnrollment, ExamResult, Examination, Student
from app.schemas import (
    ExamResultCreate, ExamResultResponse, ExaminationCreate, ExaminationResponse,
)
from app.security.identity_records import current_faculty, current_student
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/examinations", tags=["examinations"])


@router.get("/{examination_id}/roster")
def examination_roster(examination_id: str,
                       ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
                       db: Session = Depends(get_db)) -> list[dict]:
    exam = db.scalar(select(Examination).where(
        Examination.id == examination_id, Examination.tenant_id == ctx.tenant.id,
    ))
    if exam is None:
        raise HTTPException(status_code=404, detail="Examination not found in this tenant")
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        if db.scalar(select(CourseAssignment.id).where(
            CourseAssignment.tenant_id == ctx.tenant.id,
            CourseAssignment.course_id == exam.course_id,
            CourseAssignment.faculty_id == faculty.id,
        )) is None:
            raise HTTPException(status_code=403, detail="Examination course is not assigned to this faculty member")
    students = db.scalars(
        select(Student)
        .join(CourseEnrollment, CourseEnrollment.student_id == Student.id)
        .where(
            CourseEnrollment.tenant_id == ctx.tenant.id,
            CourseEnrollment.course_id == exam.course_id,
            Student.tenant_id == ctx.tenant.id,
        ).order_by(Student.registration_number)
    ).all()
    results = {result.student_id: result for result in db.scalars(select(ExamResult).where(
        ExamResult.tenant_id == ctx.tenant.id, ExamResult.examination_id == exam.id,
    )).all()}
    return [{
        "student_id": student.id,
        "registration_number": student.registration_number,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "marks_obtained": results[student.id].marks_obtained if student.id in results else None,
        "grade": results[student.id].grade if student.id in results else None,
    } for student in students]


@router.get("", response_model=list[ExaminationResponse])
def list_examinations(
    course_id: str | None = Query(default=None),
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> list[Examination]:
    """List exams and recorded marks for the active tenant."""
    query = (
        select(Examination)
        .options(joinedload(Examination.results))
        .where(Examination.tenant_id == ctx.tenant.id)
    )
    student = None
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        query = query.join(CourseAssignment, CourseAssignment.course_id == Examination.course_id).where(
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
        query = query.join(CourseEnrollment, CourseEnrollment.course_id == Examination.course_id).where(
            CourseEnrollment.tenant_id == ctx.tenant.id,
            CourseEnrollment.student_id == student.id,
        )
    if course_id:
        query = query.where(Examination.course_id == course_id)
    query = query.order_by(Examination.created_at.desc())
    exams = list(db.scalars(query).unique().all())
    from app.schemas import ExaminationResponse

    filtered = []
    for exam in exams:
        response = ExaminationResponse.model_validate(exam)
        visible_results = []
        for result in response.results:
            if result.tenant_id != ctx.tenant.id:
                continue
            result_student = db.scalar(
                select(Student.id).where(
                    Student.id == result.student_id,
                    Student.tenant_id == ctx.tenant.id,
                )
            )
            enrollment = db.scalar(
                select(CourseEnrollment.id).where(
                    CourseEnrollment.tenant_id == ctx.tenant.id,
                    CourseEnrollment.course_id == exam.course_id,
                    CourseEnrollment.student_id == result.student_id,
                )
            )
            if result_student and enrollment and (student is None or result.student_id == student.id):
                visible_results.append(result)
        response.results = visible_results
        filtered.append(response)
    return filtered


@router.post("", response_model=ExaminationResponse, status_code=status.HTTP_201_CREATED)
def create_examination(
    payload: ExaminationCreate,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
    db: Session = Depends(get_db),
) -> Examination:
    """Schedule a new exam within the active tenant."""
    course = db.scalar(
        select(Course).where(Course.id == payload.course_id, Course.tenant_id == ctx.tenant.id)
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found in this tenant")
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        assigned = db.scalar(
            select(CourseAssignment.id).where(
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.course_id == course.id,
                CourseAssignment.faculty_id == faculty.id,
            )
        )
        if assigned is None:
            raise HTTPException(status_code=403, detail="Course is not assigned to this faculty member")

    exam = Examination(
        tenant_id=ctx.tenant.id,
        course_id=payload.course_id,
        title=payload.title,
        exam_type=payload.exam_type,
        max_marks=payload.max_marks,
        date=payload.date,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return exam


@router.post("/{examination_id}/results", response_model=ExamResultResponse, status_code=status.HTTP_201_CREATED)
def record_student_result(
    examination_id: str,
    payload: ExamResultCreate,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
    db: Session = Depends(get_db),
) -> ExamResult:
    """Record marks for a student on an exam."""
    exam = db.scalar(
        select(Examination).where(
            Examination.id == examination_id,
            Examination.tenant_id == ctx.tenant.id,
        )
    )
    if not exam:
        raise HTTPException(status_code=404, detail="Examination not found in this tenant")

    student = db.scalar(
        select(Student).where(
            Student.id == payload.student_id,
            Student.tenant_id == ctx.tenant.id,
        )
    )
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this tenant")

    enrolled = db.scalar(
        select(CourseEnrollment.id).where(
            CourseEnrollment.tenant_id == ctx.tenant.id,
            CourseEnrollment.course_id == exam.course_id,
            CourseEnrollment.student_id == student.id,
        )
    )
    if enrolled is None:
        raise HTTPException(status_code=422, detail="Student is not enrolled in the examination course")
    if payload.marks_obtained > exam.max_marks:
        raise HTTPException(status_code=422, detail="Marks cannot exceed the examination maximum")
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        assigned = db.scalar(
            select(CourseAssignment.id).where(
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.course_id == exam.course_id,
                CourseAssignment.faculty_id == faculty.id,
            )
        )
        if assigned is None:
            raise HTTPException(status_code=403, detail="Examination course is not assigned to this faculty member")

    # Calculate default letter grade
    pct = (payload.marks_obtained / exam.max_marks) * 100 if exam.max_marks > 0 else 0
    grade = payload.grade or ("S" if pct >= 90 else "A" if pct >= 80 else "B" if pct >= 70 else "C" if pct >= 60 else "D" if pct >= 50 else "F")

    existing = db.scalar(
        select(ExamResult).where(
            ExamResult.examination_id == examination_id,
            ExamResult.student_id == payload.student_id,
        )
    )
    if existing:
        existing.marks_obtained = payload.marks_obtained
        existing.grade = grade
        db.commit()
        db.refresh(existing)
        return existing

    result = ExamResult(
        tenant_id=ctx.tenant.id,
        examination_id=examination_id,
        student_id=payload.student_id,
        marks_obtained=payload.marks_obtained,
        grade=grade,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
