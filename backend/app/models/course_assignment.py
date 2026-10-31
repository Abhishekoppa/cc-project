from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class CourseAssignment(Base):
    """A faculty member assigned to teach a course in one tenant."""

    __tablename__ = "course_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "course_id", "faculty_id", name="uq_course_assignment"),
        Index("ix_course_assignments_faculty", "tenant_id", "faculty_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
