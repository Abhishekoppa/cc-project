"""Add contact fields, document categories and faculty scoped documents."""
from alembic import op
import sqlalchemy as sa


revision = "0005_management_data"
down_revision = "0004_tenant_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("phone", sa.String(length=30), nullable=True))
    op.add_column("faculty", sa.Column("phone", sa.String(length=30), nullable=True))
    op.drop_constraint("ck_documents_scope", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_scope", "documents", "scope IN ('college', 'course', 'student', 'faculty')",
    )
    op.add_column(
        "documents", sa.Column("category", sa.String(length=32), nullable=False, server_default="other"),
    )
    op.create_check_constraint(
        "ck_documents_category", "documents",
        "category IN ('identity_proof', 'marksheet', 'certificate', 'admission', 'faculty', 'other')",
    )
    op.add_column(
        "documents",
        sa.Column("faculty_id", sa.String(length=36), sa.ForeignKey("faculty.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_documents_faculty", "documents", ["tenant_id", "faculty_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_faculty", table_name="documents")
    op.drop_column("documents", "faculty_id")
    op.drop_constraint("ck_documents_category", "documents", type_="check")
    op.drop_column("documents", "category")
    op.drop_constraint("ck_documents_scope", "documents", type_="check")
    op.create_check_constraint("ck_documents_scope", "documents", "scope IN ('college', 'course', 'student')")
    op.drop_column("faculty", "phone")
    op.drop_column("students", "phone")
