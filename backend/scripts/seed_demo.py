"""Idempotently seed synthetic tenants, memberships, students, faculty, courses, attendance, exams, and fees."""
from datetime import date, timedelta
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Attendance, Course, CourseAssignment, CourseEnrollment, Examination, ExamResult,
    Faculty, Fee, Membership, Student, Tenant,
)

TENANTS_DATA = [
    {
        "code": "VIT001",
        "name": "Vellore Institute of Technology",
        "slug": "vit-vellore",
        "students": [
            ("26MCA0180", "Abhishek", "Poudel", "abhishek.poudel@vit.edu", "MCA", 1),
            ("26MCA0177", "Vivek", "Kumar", "vivek.kumar@vit.edu", "MCA", 1),
            ("26MCA0101", "Ananya", "Sharma", "ananya.sharma@vit.edu", "MCA", 1),
            ("26BCE0042", "Rahul", "Verma", "rahul.verma@vit.edu", "Computer Science", 3),
        ],
        "faculty": [
            ("FAC-VIT-01", "Dr. Rajesh", "Sundaram", "rajesh.sundaram@vit.edu", "School of Computer Science", "Professor"),
            ("FAC-VIT-02", "Prof. Priya", "Natarajan", "priya.natarajan@vit.edu", "School of Computer Science", "Associate Professor"),
        ],
        "courses": [
            ("MCA501", "Cloud Computing & Multi-Tenant Architecture", 4, "MCA", 1),
            ("MCA502", "Distributed Systems & Scalability", 4, "MCA", 1),
            ("MCA503", "Enterprise Software Engineering", 3, "MCA", 1),
        ],
    },
    {
        "code": "ABC001",
        "name": "ABC Engineering College",
        "slug": "abc-engineering",
        "students": [
            ("ABC-CS-001", "Rohan", "Mehta", "rohan.mehta@abc.edu", "Computer Science", 2),
            ("ABC-CS-002", "Sneha", "Patel", "sneha.patel@abc.edu", "Computer Science", 2),
        ],
        "faculty": [
            ("FAC-ABC-01", "Dr. Arvind", "Gupta", "arvind.gupta@abc.edu", "Computer Science", "Professor"),
        ],
        "courses": [
            ("CS301", "Database Management Systems", 4, "Computer Science", 2),
            ("CS302", "Computer Networks", 3, "Computer Science", 2),
        ],
    },
    {
        "code": "XYZ001",
        "name": "XYZ Institute of Technology",
        "slug": "xyz-tech",
        "students": [
            ("XYZ-IT-101", "Karan", "Joshi", "karan.joshi@xyz.edu", "Information Technology", 4),
            ("XYZ-IT-102", "Pooja", "Nair", "pooja.nair@xyz.edu", "Information Technology", 4),
        ],
        "faculty": [
            ("FAC-XYZ-01", "Dr. Sunita", "Deshmukh", "sunita.deshmukh@xyz.edu", "Information Technology", "Professor"),
        ],
        "courses": [
            ("IT401", "Cloud Security & Governance", 3, "Information Technology", 4),
        ],
    },
]

COLLEGE_ADMIN_EMAILS = {
    "VIT001": "college.admin@vit.campuscloud.test",
    "ABC001": "college.admin@abc.campuscloud.test",
    "XYZ001": "college.admin@xyz.campuscloud.test",
}

# Keep demonstration sessions stable across repeated seed runs; rolling dates would
# silently create a new attendance batch every day.
DEMO_AS_OF = date(2026, 10, 31)


def ensure_membership(session, tenant: Tenant, subject: str, role: str) -> None:
    membership = session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.subject == subject,
        )
    )
    if membership is None:
        session.add(Membership(tenant_id=tenant.id, subject=subject, role=role, status="active"))
    elif membership.role != role or membership.status != "active":
        membership.role = role
        membership.status = "active"


def seed() -> None:
    with SessionLocal.begin() as session:
        for t_info in TENANTS_DATA:
            code = t_info["code"]
            tenant = session.scalar(select(Tenant).where(Tenant.tenant_code == code))
            if tenant is None:
                tenant = Tenant(tenant_code=code, name=t_info["name"], slug=t_info["slug"], status="active")
                session.add(tenant)
                session.flush()

            # Seed Admin Memberships
            admin_subject = f"demo:{code}:college-admin"
            ensure_membership(session, tenant, admin_subject, "college_admin")
            ensure_membership(session, tenant, COLLEGE_ADMIN_EMAILS[code], "college_admin")

            # The local super-admin demo identity is represented by a real seeded role.
            if code == "VIT001":
                ensure_membership(session, tenant, "demo-platform-admin", "platform_admin")

            # Universal demo admin user access
            global_admin_subject = "demo-admin"
            if not session.scalar(select(Membership).where(Membership.tenant_id == tenant.id, Membership.subject == global_admin_subject)):
                session.add(Membership(tenant_id=tenant.id, subject=global_admin_subject, role="college_admin", status="active"))

            # Seed Students
            students_map = {}
            for reg, fn, ln, em, dept, sem in t_info["students"]:
                std = session.scalar(select(Student).where(Student.tenant_id == tenant.id, Student.registration_number == reg))
                if not std:
                    std = Student(
                        tenant_id=tenant.id,
                        registration_number=reg,
                        first_name=fn,
                        last_name=ln,
                        email=em,
                        department=dept,
                        semester=sem,
                        date_of_birth=date(2002, 5, 15),
                        enrollment_date=date(2025, 8, 1),
                        status="active",
                    )
                    session.add(std)
                    session.flush()
                students_map[reg] = std
                ensure_membership(session, tenant, em.strip().lower(), "student")

            # Seed Faculty
            faculty_list = []
            for emp_id, fn, ln, em, dept, desig in t_info["faculty"]:
                fac = session.scalar(select(Faculty).where(Faculty.tenant_id == tenant.id, Faculty.employee_id == emp_id))
                if not fac:
                    fac = Faculty(
                        tenant_id=tenant.id,
                        employee_id=emp_id,
                        first_name=fn,
                        last_name=ln,
                        email=em,
                        department=dept,
                        designation=desig,
                        status="active",
                    )
                    session.add(fac)
                    session.flush()
                faculty_list.append(fac)
                ensure_membership(session, tenant, em.strip().lower(), "faculty")

            # Seed Courses
            courses_map = {}
            for c_code, title, crd, dept, sem in t_info["courses"]:
                crs = session.scalar(select(Course).where(Course.tenant_id == tenant.id, Course.course_code == c_code))
                if not crs:
                    crs = Course(
                        tenant_id=tenant.id,
                        course_code=c_code,
                        title=title,
                        credits=crd,
                        department=dept,
                        semester=sem,
                        status="active",
                    )
                    session.add(crs)
                    session.flush()
                courses_map[c_code] = crs

            # Provision role-scoped course access for the demo identities.
            course_list = list(courses_map.values())
            for index, course in enumerate(course_list):
                if faculty_list:
                    faculty = faculty_list[index % len(faculty_list)]
                    assignment = session.scalar(
                        select(CourseAssignment).where(
                            CourseAssignment.tenant_id == tenant.id,
                            CourseAssignment.course_id == course.id,
                            CourseAssignment.faculty_id == faculty.id,
                        )
                    )
                    if assignment is None:
                        session.add(CourseAssignment(
                            tenant_id=tenant.id, course_id=course.id, faculty_id=faculty.id,
                        ))
                for student in students_map.values():
                    if student.department == course.department and (
                        course.semester is None or student.semester == course.semester
                    ):
                        enrollment = session.scalar(
                            select(CourseEnrollment).where(
                                CourseEnrollment.tenant_id == tenant.id,
                                CourseEnrollment.course_id == course.id,
                                CourseEnrollment.student_id == student.id,
                            )
                        )
                        if enrollment is None:
                            session.add(CourseEnrollment(
                                tenant_id=tenant.id, course_id=course.id, student_id=student.id,
                            ))
            session.flush()
            # Legacy demo attendance and exam data is recorded against every student
            # in the first sample course; preserve it as explicit enrollment data.
            if course_list:
                first_course = course_list[0]
                for student in students_map.values():
                    enrollment = session.scalar(
                        select(CourseEnrollment).where(
                            CourseEnrollment.tenant_id == tenant.id,
                            CourseEnrollment.course_id == first_course.id,
                            CourseEnrollment.student_id == student.id,
                        )
                    )
                    if enrollment is None:
                        session.add(CourseEnrollment(
                            tenant_id=tenant.id,
                            course_id=first_course.id,
                            student_id=student.id,
                        ))

            # Seed course-by-course attendance and assessments only for actual
            # tenant enrollments. Stable dates and natural-key lookups keep this
            # block idempotent even when run repeatedly on different days.
            today = DEMO_AS_OF
            for course_index, course in enumerate(course_list):
                enrolled_students = list(session.scalars(
                    select(Student)
                    .join(CourseEnrollment, CourseEnrollment.student_id == Student.id)
                    .where(
                        CourseEnrollment.tenant_id == tenant.id,
                        CourseEnrollment.course_id == course.id,
                        Student.tenant_id == tenant.id,
                    )
                    .order_by(Student.registration_number)
                ).all())
                assigned_faculty = list(session.scalars(
                    select(Faculty)
                    .join(CourseAssignment, CourseAssignment.faculty_id == Faculty.id)
                    .where(
                        CourseAssignment.tenant_id == tenant.id,
                        CourseAssignment.course_id == course.id,
                        Faculty.tenant_id == tenant.id,
                    )
                    .order_by(Faculty.employee_id)
                ).all())
                faculty_id = assigned_faculty[0].id if assigned_faculty else None

                for student_index, student in enumerate(enrolled_students):
                    for session_index, day_offset in enumerate((4, 6, 8, 11, 13, 15)):
                        attendance_date = today - timedelta(days=day_offset)
                        existing = session.scalar(select(Attendance.id).where(
                            Attendance.tenant_id == tenant.id,
                            Attendance.student_id == student.id,
                            Attendance.course_id == course.id,
                            Attendance.date == attendance_date,
                        ))
                        if existing is None:
                            status = "absent" if (student_index + session_index + course_index) % 5 == 0 else (
                                "late" if (student_index + session_index) % 7 == 0 else "present"
                            )
                            session.add(Attendance(
                                tenant_id=tenant.id, student_id=student.id, course_id=course.id,
                                faculty_id=faculty_id, date=attendance_date, status=status,
                            ))

                completed_title = "Mid-Term Examination 2026"
                completed_exam = session.scalar(select(Examination).where(
                    Examination.tenant_id == tenant.id,
                    Examination.course_id == course.id,
                    Examination.title == completed_title,
                ))
                if completed_exam is None:
                    completed_exam = Examination(
                        tenant_id=tenant.id, course_id=course.id, title=completed_title,
                        exam_type="midterm", max_marks=100, date=date(2026, 10, 15),
                    )
                    session.add(completed_exam)
                    session.flush()
                elif completed_exam.date is None:
                    completed_exam.date = date(2026, 10, 15)

                for student_index, student in enumerate(enrolled_students):
                    if session.scalar(select(ExamResult.id).where(
                        ExamResult.tenant_id == tenant.id,
                        ExamResult.examination_id == completed_exam.id,
                        ExamResult.student_id == student.id,
                    )) is None:
                        marks = float(72 + ((student_index * 7 + course_index * 3) % 27))
                        grade = "S" if marks >= 90 else "A" if marks >= 80 else "B" if marks >= 70 else "C"
                        session.add(ExamResult(
                            tenant_id=tenant.id, examination_id=completed_exam.id,
                            student_id=student.id, marks_obtained=marks, grade=grade,
                        ))

                upcoming_title = "Final Examination 2026"
                upcoming_exam = session.scalar(select(Examination).where(
                    Examination.tenant_id == tenant.id,
                    Examination.course_id == course.id,
                    Examination.title == upcoming_title,
                ))
                if upcoming_exam is None:
                    session.add(Examination(
                        tenant_id=tenant.id, course_id=course.id, title=upcoming_title,
                        exam_type="final", max_marks=100, date=date(2026, 11, 18),
                    ))

            # Seed Sample Fees
            for i, std in enumerate(students_map.values()):
                fee_desc = "Semester 1 Tuition & Lab Fee"
                if not session.scalar(select(Fee).where(Fee.tenant_id == tenant.id, Fee.student_id == std.id, Fee.description == fee_desc)):
                    paid = (i % 2 == 0)
                    session.add(Fee(
                        tenant_id=tenant.id,
                        student_id=std.id,
                        description=fee_desc,
                        amount=65000.0,
                        due_date=today - timedelta(days=10),
                        paid_date=today - timedelta(days=15) if paid else None,
                        status="paid" if paid else "pending",
                    ))

    print("Demo data seeded successfully for VIT001, ABC001, and XYZ001!")


if __name__ == "__main__":
    seed()
