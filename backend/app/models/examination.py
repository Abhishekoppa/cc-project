from datetime import date as dt_date, datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
    String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Examination(Base):
    __tablename__ = "examinations"
    __table_args__ = (
        CheckConstraint(
            "exam_type IN ('midterm', 'final', 'quiz', 'assignment')",
            name="ck_examinations_type",
        ),
        Index("ix_examinations_tenant", "tenant_id"),
        Index("ix_examinations_course", "course_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    exam_type: Mapped[str] = mapped_column(String(20), nullable=False)
    max_marks: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    date: Mapped[dt_date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant: Mapped["Tenant"] = relationship()
    course: Mapped["Course"] = relationship()
    results: Mapped[list["ExamResult"]] = relationship(back_populates="examination", cascade="all, delete-orphan")


class ExamResult(Base):
    __tablename__ = "exam_results"
    __table_args__ = (
        UniqueConstraint("examination_id", "student_id", name="uq_exam_results_exam_student"),
        Index("ix_exam_results_tenant", "tenant_id"),
        Index("ix_exam_results_student", "student_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    examination_id: Mapped[str] = mapped_column(ForeignKey("examinations.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    marks_obtained: Mapped[float] = mapped_column(Float, nullable=False)
    grade: Mapped[str | None] = mapped_column(String(5), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant: Mapped["Tenant"] = relationship()
    examination: Mapped["Examination"] = relationship(back_populates="results")
    student: Mapped["Student"] = relationship()
