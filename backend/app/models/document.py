from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Document(Base):
    """Tenant-scoped document metadata; object bytes remain in private S3."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "object_key", name="uq_documents_tenant_key"),
        CheckConstraint("scope IN ('college', 'course', 'student', 'faculty')", name="ck_documents_scope"),
        CheckConstraint("status IN ('pending', 'active')", name="ck_documents_status"),
        CheckConstraint("category IN ('identity_proof', 'marksheet', 'certificate', 'admission', 'faculty', 'other')", name="ck_documents_category"),
        CheckConstraint("file_size > 0", name="ck_documents_file_size"),
        Index("ix_documents_tenant_scope", "tenant_id", "scope", "status"),
        Index("ix_documents_course", "tenant_id", "course_id"),
        Index("ix_documents_student", "tenant_id", "student_id"),
        Index("ix_documents_faculty", "tenant_id", "faculty_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="college", server_default="college")
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=True)
    student_id: Mapped[str | None] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=True)
    faculty_id: Mapped[str | None] = mapped_column(ForeignKey("faculty.id", ondelete="CASCADE"), nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="other", server_default="other")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
