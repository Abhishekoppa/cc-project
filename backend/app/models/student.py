from datetime import date as dt_date, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("tenant_id", "registration_number", name="uq_students_tenant_reg"),
        UniqueConstraint("tenant_id", "auth_subject", name="uq_students_tenant_subject"),
        CheckConstraint("status IN ('active', 'inactive', 'graduated')", name="ck_students_status"),
        Index("ix_students_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    registration_number: Mapped[str] = mapped_column(String(50), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    auth_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_of_birth: Mapped[dt_date | None] = mapped_column(Date, nullable=True)
    enrollment_date: Mapped[dt_date | None] = mapped_column(Date, nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    semester: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant: Mapped["Tenant"] = relationship()
