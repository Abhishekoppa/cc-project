from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Attendance, Course, CourseEnrollment, Document, Examination, Faculty, Fee, Student, Tenant
from app.schemas import TenantAnalyticsReport
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/analytics", response_model=TenantAnalyticsReport)
def get_tenant_analytics(
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> TenantAnalyticsReport:
    """Generate live analytics strictly isolated for the active tenant."""
    tid = ctx.tenant.id

    total_students = db.scalar(
        select(func.count(Student.id)).where(Student.tenant_id == tid, Student.status == "active")
    ) or 0

    total_faculty = db.scalar(
        select(func.count(Faculty.id)).where(Faculty.tenant_id == tid, Faculty.status == "active")
    ) or 0

    total_courses = db.scalar(
        select(func.count(Course.id)).where(Course.tenant_id == tid, Course.status == "active")
    ) or 0

    total_attendance = db.scalar(
        select(func.count(Attendance.id)).where(Attendance.tenant_id == tid)
    ) or 0

    present_attendance = db.scalar(
        select(func.count(Attendance.id)).where(Attendance.tenant_id == tid, Attendance.status == "present")
    ) or 0

    late_attendance = db.scalar(
        select(func.count(Attendance.id)).where(Attendance.tenant_id == tid, Attendance.status == "late")
    ) or 0
    attendance_rate = (
        round(((present_attendance + late_attendance) / total_attendance) * 100, 2)
        if total_attendance > 0
        else 0.0
    )

    total_fees_billed = db.scalar(
        select(func.coalesce(func.sum(Fee.amount), 0.0)).where(Fee.tenant_id == tid)
    ) or 0.0

    total_fees_collected = db.scalar(
        select(func.coalesce(func.sum(Fee.amount), 0.0)).where(Fee.tenant_id == tid, Fee.status == "paid")
    ) or 0.0

    fee_rate = (
        round((total_fees_collected / total_fees_billed) * 100, 2)
        if total_fees_billed > 0
        else 0.0
    )

    active_exams = db.scalar(
        select(func.count(Examination.id)).where(Examination.tenant_id == tid)
    ) or 0
    total_enrollments = db.scalar(select(func.count(CourseEnrollment.id)).where(CourseEnrollment.tenant_id == tid)) or 0
    todays_attendance = db.scalar(select(func.count(Attendance.id)).where(
        Attendance.tenant_id == tid, Attendance.date == date.today(),
    )) or 0
    upcoming_exams = db.scalar(select(func.count(Examination.id)).where(
        Examination.tenant_id == tid, Examination.date >= date.today(),
    )) or 0
    uploaded_documents = db.scalar(select(func.count(Document.id)).where(
        Document.tenant_id == tid, Document.status == "active",
    )) or 0

    return TenantAnalyticsReport(
        tenant_code=ctx.tenant.tenant_code,
        tenant_name=ctx.tenant.name,
        total_students=total_students,
        total_faculty=total_faculty,
        total_courses=total_courses,
        total_attendance_records=total_attendance,
        attendance_rate_percent=attendance_rate,
        total_fees_billed=float(total_fees_billed),
        total_fees_collected=float(total_fees_collected),
        fee_collection_rate_percent=fee_rate,
        active_examinations=active_exams,
        total_enrollments=total_enrollments,
        todays_attendance=todays_attendance,
        upcoming_examinations=upcoming_exams,
        uploaded_documents=uploaded_documents,
    )


@router.get("/multi-tenant-summary")
def get_multi_tenant_platform_summary(
    ctx: TenantContext = Depends(require_roles("platform_admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Summary of all colleges on the shared infrastructure, verifying tenant isolation."""
    if ctx.role != "platform_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform administrator access required")
    tenants = db.scalars(select(Tenant).where(Tenant.status == "active").order_by(Tenant.tenant_code)).all()
    results = []

    for t in tenants:
        std_count = db.scalar(select(func.count(Student.id)).where(Student.tenant_id == t.id)) or 0
        fac_count = db.scalar(select(func.count(Faculty.id)).where(Faculty.tenant_id == t.id)) or 0
        crs_count = db.scalar(select(func.count(Course.id)).where(Course.tenant_id == t.id)) or 0
        fee_collected = db.scalar(select(func.coalesce(func.sum(Fee.amount), 0.0)).where(Fee.tenant_id == t.id, Fee.status == "paid")) or 0.0

        results.append({
            "tenant_code": t.tenant_code,
            "tenant_name": t.name,
            "student_count": std_count,
            "faculty_count": fac_count,
            "course_count": crs_count,
            "fees_collected": float(fee_collected),
        })

    return results
