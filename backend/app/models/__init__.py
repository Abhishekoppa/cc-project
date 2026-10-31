from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.attendance import Attendance  # noqa: E402,F401
from app.models.course import Course  # noqa: E402,F401
from app.models.course_assignment import CourseAssignment  # noqa: E402,F401
from app.models.course_enrollment import CourseEnrollment  # noqa: E402,F401
from app.models.document import Document  # noqa: E402,F401
from app.models.examination import Examination, ExamResult  # noqa: E402,F401
from app.models.faculty import Faculty  # noqa: E402,F401
from app.models.fee import Fee  # noqa: E402,F401
from app.models.membership import Membership  # noqa: E402,F401
from app.models.local_account import LocalAccount  # noqa: E402,F401
from app.models.registration_intent import RegistrationIntent  # noqa: E402,F401
from app.models.student import Student  # noqa: E402,F401
from app.models.tenant import Tenant  # noqa: E402,F401
