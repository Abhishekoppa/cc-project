from datetime import date as dt_date, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint("tenant_id", "student_id", "course_id", "date", name="uq_attendance_record"),
        CheckConstraint("status IN ('present', 'absent', 'late')", name="ck_attendance_status"),
        Index("ix_attendance_tenant_date", "tenant_id", "date"),
        Index("ix_attendance_student", "student_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    faculty_id: Mapped[str | None] = mapped_column(ForeignKey("faculty.id", ondelete="SET NULL"), nullable=True)
    date: Mapped[dt_date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="present")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant: Mapped["Tenant"] = relationship()
    student: Mapped["Student"] = relationship()
    course: Mapped["Course"] = relationship()
