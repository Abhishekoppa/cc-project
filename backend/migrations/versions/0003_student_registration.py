"""Add tenant-scoped local accounts and Cognito registration intents."""
from alembic import op
import sqlalchemy as sa

revision = "0003_student_registration"
down_revision = "0002_college_modules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "email", name="uq_local_accounts_tenant_email"),
    )
    op.create_index("ix_local_accounts_tenant", "local_accounts", ["tenant_id"])

    op.create_table(
        "registration_intents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cognito_subject", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("cognito_subject", name="uq_registration_intents_subject"),
        sa.UniqueConstraint("email", name="uq_registration_intents_email"),
        sa.CheckConstraint(
            "status IN ('pending_confirmation', 'confirmed', 'completed')",
            name="ck_registration_intents_status",
        ),
    )
    op.create_index("ix_registration_intents_tenant_status", "registration_intents", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_registration_intents_tenant_status", table_name="registration_intents")
    op.drop_table("registration_intents")
    op.drop_index("ix_local_accounts_tenant", table_name="local_accounts")
    op.drop_table("local_accounts")
