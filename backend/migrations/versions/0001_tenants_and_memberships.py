"""Create tenant registry and memberships."""
from alembic import op
import sqlalchemy as sa

revision = "0001_tenants_memberships"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_code", name="uq_tenants_tenant_code"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),
    )
    op.create_index("ix_tenants_status", "tenants", ["status"])
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "subject", name="uq_memberships_tenant_subject"),
        sa.CheckConstraint("role IN ('platform_admin', 'college_admin', 'faculty', 'student')", name="ck_memberships_role"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_memberships_status"),
    )
    op.create_index("ix_memberships_subject", "memberships", ["subject"])
    op.create_index("ix_memberships_tenant_status", "memberships", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_memberships_tenant_status", table_name="memberships")
    op.drop_index("ix_memberships_subject", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("ix_tenants_status", table_name="tenants")
    op.drop_table("tenants")
