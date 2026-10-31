"""Create college management modules: students, faculty, courses, attendance, exams, fees."""
from alembic import op
import sqlalchemy as sa

revision = "0002_college_modules"
down_revision = "0001_tenants_memberships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Students table
    op.create_table(
        "students",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("registration_number", sa.String(length=50), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("enrollment_date", sa.Date(), nullable=True),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("semester", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "registration_number", name="uq_students_tenant_reg"),
        sa.CheckConstraint("status IN ('active', 'inactive', 'graduated')", name="ck_students_status"),
    )
    op.create_index("ix_students_tenant_status", "students", ["tenant_id", "status"])

    # 2. Faculty table
    op.create_table(
        "faculty",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.String(length=50), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("designation", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "employee_id", name="uq_faculty_tenant_emp"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_faculty_status"),
    )
    op.create_index("ix_faculty_tenant_status", "faculty", ["tenant_id", "status"])

    # 3. Courses table
    op.create_table(
        "courses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_code", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "course_code", name="uq_courses_tenant_code"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_courses_status"),
    )
    op.create_index("ix_courses_tenant_status", "courses", ["tenant_id", "status"])

    # 4. Attendance table
    op.create_table(
        "attendance",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.String(length=36), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("faculty_id", sa.String(length=36), sa.ForeignKey("faculty.id", ondelete="SET NULL"), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="present"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "student_id", "course_id", "date", name="uq_attendance_record"),
        sa.CheckConstraint("status IN ('present', 'absent', 'late')", name="ck_attendance_status"),
    )
    op.create_index("ix_attendance_tenant_date", "attendance", ["tenant_id", "date"])
    op.create_index("ix_attendance_student", "attendance", ["student_id"])

    # 5. Examinations table
    op.create_table(
        "examinations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(length=36), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("exam_type", sa.String(length=20), nullable=False),
        sa.Column("max_marks", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("exam_type IN ('midterm', 'final', 'quiz', 'assignment')", name="ck_examinations_type"),
    )
    op.create_index("ix_examinations_tenant", "examinations", ["tenant_id"])
    op.create_index("ix_examinations_course", "examinations", ["course_id"])

    # 6. Exam Results table
    op.create_table(
        "exam_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("examination_id", sa.String(length=36), sa.ForeignKey("examinations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.String(length=36), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marks_obtained", sa.Float(), nullable=False),
        sa.Column("grade", sa.String(length=5), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("examination_id", "student_id", name="uq_exam_results_exam_student"),
    )
    op.create_index("ix_exam_results_tenant", "exam_results", ["tenant_id"])
    op.create_index("ix_exam_results_student", "exam_results", ["student_id"])

    # 7. Fees table
    op.create_table(
        "fees",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.String(length=36), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("paid_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'paid', 'overdue')", name="ck_fees_status"),
    )
    op.create_index("ix_fees_tenant_status", "fees", ["tenant_id", "status"])
    op.create_index("ix_fees_student", "fees", ["student_id"])


def downgrade() -> None:
    op.drop_table("fees")
    op.drop_table("exam_results")
    op.drop_table("examinations")
    op.drop_table("attendance")
    op.drop_table("courses")
    op.drop_table("faculty")
    op.drop_table("students")
