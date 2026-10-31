from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class RegistrationIntent(Base):
    """Cognito sign-up state pending email confirmation and student membership creation."""

    __tablename__ = "registration_intents"
    __table_args__ = (
        UniqueConstraint("cognito_subject", name="uq_registration_intents_subject"),
        UniqueConstraint("email", name="uq_registration_intents_email"),
        CheckConstraint(
            "status IN ('pending_confirmation', 'confirmed', 'completed')",
            name="ck_registration_intents_status",
        ),
        Index("ix_registration_intents_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    cognito_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_confirmation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
