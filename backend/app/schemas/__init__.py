from datetime import date as dt_date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ==================== TENANTS ====================
class TenantBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_code: str = Field(..., min_length=1, max_length=32, examples=["VIT001"])
    name: str = Field(..., min_length=1, max_length=200, examples=["Vellore Institute of Technology"])
    slug: str = Field(..., min_length=1, max_length=100, examples=["vit-vellore"])

    @field_validator("tenant_code", "name", "slug")
    @classmethod
    def strip_tenant_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tenant fields cannot be empty")
        return normalized


class TenantCreate(TenantBase):
    pass


class TenantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["active", "suspended"] | None = None

    @model_validator(mode="after")
    def require_update(self) -> "TenantUpdate":
        if self.name is None and self.slug is None and self.status is None:
            raise ValueError("At least one tenant field must be updated")
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("Tenant name cannot be empty")
        if self.slug is not None:
            self.slug = self.slug.strip().lower()
            if not self.slug:
                raise ValueError("Tenant slug cannot be empty")
        return self


class TenantResponse(TenantBase):
    id: str
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ==================== STUDENTS ====================
class StudentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_number: str = Field(..., max_length=50, examples=["26MCA0180"])
    first_name: str = Field(..., max_length=100, examples=["Abhishek"])
    last_name: str = Field(..., max_length=100, examples=["Poudel"])
    email: str = Field(..., max_length=254, examples=["abhishek.poudel@vit.edu"])
    phone: str | None = Field(default=None, max_length=30)
    date_of_birth: dt_date | None = None
    enrollment_date: dt_date | None = None
    department: str | None = Field(default="Computer Science", max_length=100)
    semester: int | None = Field(default=1, ge=1, le=12)


class StudentCreate(StudentBase):
    pass


class StudentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_number: str | None = Field(default=None, max_length=50)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    date_of_birth: dt_date | None = None
    enrollment_date: dt_date | None = None
    department: str | None = Field(default=None, max_length=100)
    semester: int | None = Field(default=None, ge=1, le=12)
    status: Literal["active", "inactive", "graduated"] | None = None


class StudentResponse(StudentBase):
    id: str
    tenant_id: str
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ==================== FACULTY ====================
class FacultyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str = Field(..., max_length=50, examples=["EMP1001"])
    first_name: str = Field(..., max_length=100, examples=["Ramesh"])
    last_name: str = Field(..., max_length=100, examples=["Kumar"])
    email: str = Field(..., max_length=254, examples=["ramesh.kumar@vit.edu"])
    phone: str | None = Field(default=None, max_length=30)
    department: str = Field(..., max_length=100, examples=["Computer Science"])
    designation: str | None = Field(default="Assistant Professor", max_length=100)


class FacultyCreate(FacultyBase):
    auth_subject: str | None = Field(default=None, max_length=255)


class FacultyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str | None = Field(default=None, max_length=50)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    department: str | None = Field(default=None, max_length=100)
    designation: str | None = Field(default=None, max_length=100)
    status: Literal["active", "inactive"] | None = None


class FacultyResponse(FacultyBase):
    id: str
    tenant_id: str
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ==================== COURSES ====================
class CourseBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_code: str = Field(..., max_length=20, examples=["CS501"])
    title: str = Field(..., max_length=200, examples=["Cloud Computing Architecture"])
    description: str | None = None
    credits: int = Field(default=3, ge=1, le=10)
    department: str = Field(..., max_length=100, examples=["Computer Science"])
    semester: int | None = Field(default=1, ge=1, le=12)


class CourseCreate(CourseBase):
    pass


class CourseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_code: str | None = Field(default=None, max_length=20)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    credits: int | None = Field(default=None, ge=1, le=10)
    department: str | None = Field(default=None, max_length=100)
    semester: int | None = Field(default=None, ge=1, le=12)
    status: Literal["active", "archived"] | None = None


class CourseFacultyAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    faculty_id: str


class CourseStudentEnrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str


class ExamResultUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marks_obtained: float = Field(..., ge=0)
    grade: str | None = None


class CourseResponse(CourseBase):
    id: str
    tenant_id: str
    status: str
    created_at: datetime
    faculty_names: list[str] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


# ==================== ATTENDANCE ====================
class AttendanceRecord(BaseModel):
    student_id: str
    course_id: str
    faculty_id: str | None = None
    date: dt_date
    status: Literal["present", "absent", "late"] = "present"


class AttendanceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str
    status: Literal["present", "absent", "late"] = "present"


class AttendanceBulkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str
    date: dt_date
    faculty_id: str | None = None
    records: list[AttendanceEntry] = Field(
        ...,
        min_length=1,
    )


class AttendanceResponse(BaseModel):
    id: str
    tenant_id: str
    student_id: str
    course_id: str
    faculty_id: str | None
    date: dt_date
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ==================== EXAMINATIONS ====================
class ExaminationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str
    title: str = Field(..., max_length=200, examples=["Mid-Term Examination 2026"])
    exam_type: Literal["midterm", "final", "quiz", "assignment"] = "midterm"
    max_marks: int = Field(default=100, ge=1)
    date: dt_date | None = None


class ExaminationCreate(ExaminationBase):
    pass


class ExamResultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str
    marks_obtained: float = Field(..., ge=0)
    grade: str | None = None


class ExamResultResponse(BaseModel):
    id: str
    tenant_id: str
    examination_id: str
    student_id: str
    marks_obtained: float
    grade: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ExaminationResponse(ExaminationBase):
    id: str
    tenant_id: str
    created_at: datetime
    results: list[ExamResultResponse] = []
    model_config = ConfigDict(from_attributes=True)


# ==================== FEES ====================
class FeeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str
    description: str = Field(..., max_length=200, examples=["Semester 1 Tuition Fee"])
    amount: float = Field(..., gt=0, examples=[55000.0])
    due_date: dt_date


class FeeUpdateStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending", "paid", "overdue"]
    paid_date: dt_date | None = None


class FeeResponse(BaseModel):
    id: str
    tenant_id: str
    student_id: str
    description: str
    amount: float
    due_date: dt_date
    paid_date: dt_date | None
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ==================== REPORTS ====================
class TenantAnalyticsReport(BaseModel):
    tenant_code: str
    tenant_name: str
    total_students: int
    total_faculty: int
    total_courses: int
    total_attendance_records: int
    attendance_rate_percent: float
    total_fees_billed: float
    total_fees_collected: float
    fee_collection_rate_percent: float
    active_examinations: int
    total_enrollments: int = 0
    todays_attendance: int = 0
    upcoming_examinations: int = 0
    uploaded_documents: int = 0
