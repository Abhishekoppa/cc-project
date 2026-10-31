from datetime import date as dt_date, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Fee(Base):
    __tablename__ = "fees"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'paid', 'overdue')", name="ck_fees_status"),
        Index("ix_fees_tenant_status", "tenant_id", "status"),
        Index("ix_fees_student", "student_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    due_date: Mapped[dt_date] = mapped_column(Date, nullable=False)
    paid_date: Mapped[dt_date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tenant: Mapped["Tenant"] = relationship()
    student: Mapped["Student"] = relationship()
