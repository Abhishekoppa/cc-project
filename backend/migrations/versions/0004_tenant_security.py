"""Add role-scope relationships and tenant document metadata."""
from alembic import op
import sqlalchemy as sa


revision = "0004_tenant_security"
down_revision = "0003_student_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("auth_subject", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_students_tenant_subject", "students", ["tenant_id", "auth_subject"])
    op.add_column("faculty", sa.Column("auth_subject", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_faculty_tenant_subject", "faculty", ["tenant_id", "auth_subject"])

    op.create_table(
        "course_assignments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("faculty_id", sa.String(length=36), sa.ForeignKey("faculty.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "course_id", "faculty_id", name="uq_course_assignment"),
    )
    op.create_index("ix_course_assignments_faculty", "course_assignments", ["tenant_id", "faculty_id"])

    op.create_table(
        "course_enrollments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.String(length=36), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "course_id", "student_id", name="uq_course_enrollment"),
    )
    op.create_index("ix_course_enrollments_student", "course_enrollments", ["tenant_id", "student_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="college"),
        sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=True),
        sa.Column("student_id", sa.String(length=36), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "object_key", name="uq_documents_tenant_key"),
        sa.CheckConstraint("scope IN ('college', 'course', 'student')", name="ck_documents_scope"),
        sa.CheckConstraint("status IN ('pending', 'active')", name="ck_documents_status"),
        sa.CheckConstraint("file_size > 0", name="ck_documents_file_size"),
    )
    op.create_index("ix_documents_tenant_scope", "documents", ["tenant_id", "scope", "status"])
    op.create_index("ix_documents_course", "documents", ["tenant_id", "course_id"])
    op.create_index("ix_documents_student", "documents", ["tenant_id", "student_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_student", table_name="documents")
    op.drop_index("ix_documents_course", table_name="documents")
    op.drop_index("ix_documents_tenant_scope", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_course_enrollments_student", table_name="course_enrollments")
    op.drop_table("course_enrollments")
    op.drop_index("ix_course_assignments_faculty", table_name="course_assignments")
    op.drop_table("course_assignments")
    op.drop_constraint("uq_faculty_tenant_subject", "faculty", type_="unique")
    op.drop_column("faculty", "auth_subject")
    op.drop_constraint("uq_students_tenant_subject", "students", type_="unique")
    op.drop_column("students", "auth_subject")
