"""Tenant-scoped CSV preview and commit endpoints for academic data."""
import csv
from datetime import date
from io import StringIO
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Attendance, Course, CourseAssignment, CourseEnrollment, ExamResult, Examination, Faculty, Student
from app.schemas import CourseCreate, FacultyCreate, StudentCreate
from app.security.identity_records import current_faculty
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/imports", tags=["data-imports"])
ImportKind = Literal["students", "faculty", "courses", "attendance", "marks"]


class CsvImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv_text: str = Field(min_length=1, max_length=2_000_000)


def _parse_rows(content: str) -> tuple[list[tuple[int, dict[str, str]]], list[dict]]:
    reader = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames:
        return [], [{"row": 1, "errors": ["CSV must contain a header row"]}]
    records = []
    errors = []
    for line, raw in enumerate(reader, start=2):
        if line > 5001:
            errors.append({"row": line, "errors": ["Imports are limited to 5000 rows"]})
            break
        if None in raw:
            errors.append({"row": line, "errors": ["Row has more values than the CSV header"]})
            continue
        if not raw or all(value is None or not value.strip() for value in raw.values()):
            continue
        record = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        records.append((line, record))
    if not records and not errors:
        errors.append({"row": 1, "errors": ["CSV contains no data rows"]})
    return records, errors


def _name_parts(row: dict[str, str]) -> tuple[str, str]:
    full = row.get("name", "").strip()
    first = row.get("first_name", "").strip()
    last = row.get("last_name", "").strip()
    if full and not first:
        parts = full.split()
        first, last = parts[0], " ".join(parts[1:]) or parts[0]
    return first, last


def _validate(kind: ImportKind, content: str, ctx: TenantContext, db: Session):
    if kind in {"students", "faculty", "courses"} and ctx.role != "college_admin":
        raise HTTPException(status_code=403, detail="Only college administrators may import college records")
    records, parse_errors = _parse_rows(content)
    errors = list(parse_errors)
    normalized = []
    seen: set[tuple] = set()
    faculty = current_faculty(ctx, db) if ctx.role == "faculty" else None

    for line, row in records:
        try:
            if kind == "students":
                first, last = _name_parts(row)
                reg = row.get("student_id") or row.get("registration_number") or ""
                data = {
                    "registration_number": reg, "first_name": first, "last_name": last,
                    "email": row.get("email", "").lower(), "phone": row.get("phone") or None,
                    "department": row.get("department") or "Computer Science",
                    "semester": int(row["semester"]) if row.get("semester") else 1,
                    "enrollment_date": date.fromisoformat(row["enrollment_date"]) if row.get("enrollment_date") else None,
                }
                if not data["first_name"] or not data["last_name"]:
                    raise ValueError("Name is required")
                if "@" not in data["email"]:
                    raise ValueError("A valid email address is required")
                StudentCreate.model_validate(data)
                key = (kind, reg.casefold())
                if not reg:
                    raise ValueError("Student ID is required")
                if db.scalar(select(Student.id).where(Student.tenant_id == ctx.tenant.id, Student.registration_number == reg)):
                    raise ValueError(f"Student ID '{reg}' already exists in this college")
                if db.scalar(select(Student.id).where(Student.tenant_id == ctx.tenant.id, func.lower(Student.email) == data["email"].casefold())):
                    raise ValueError("Email already exists in this college")
                email_key = (kind, "email", data["email"].casefold())
                if email_key in seen:
                    raise ValueError("Email appears more than once in this CSV")
                seen.add(email_key)
                normalized.append((line, data))
            elif kind == "faculty":
                first, last = _name_parts(row)
                emp = row.get("faculty_id") or row.get("employee_id") or ""
                data = {"employee_id": emp, "first_name": first, "last_name": last,
                        "email": row.get("email", "").lower(), "phone": row.get("phone") or None,
                        "department": row.get("department", ""),
                        "designation": row.get("designation") or "Assistant Professor"}
                if not first or not last or not emp:
                    raise ValueError("Faculty ID and name are required")
                if not data["department"]:
                    raise ValueError("Department is required")
                if "@" not in data["email"]:
                    raise ValueError("A valid email address is required")
                FacultyCreate.model_validate(data)
                key = (kind, emp.casefold())
                if db.scalar(select(Faculty.id).where(Faculty.tenant_id == ctx.tenant.id, Faculty.employee_id == emp)):
                    raise ValueError(f"Faculty ID '{emp}' already exists in this college")
                if db.scalar(select(Faculty.id).where(Faculty.tenant_id == ctx.tenant.id, func.lower(Faculty.email) == data["email"].casefold())):
                    raise ValueError("Email already exists in this college")
                email_key = (kind, "email", data["email"].casefold())
                if email_key in seen:
                    raise ValueError("Email appears more than once in this CSV")
                seen.add(email_key)
                normalized.append((line, data))
            elif kind == "courses":
                code = (row.get("course_code") or "").strip().upper()
                data = {
                    "course_code": code,
                    "title": row.get("title") or row.get("course_name") or "",
                    "description": row.get("description") or None,
                    "department": row.get("department") or "",
                    "credits": int(row["credits"]) if row.get("credits") else 3,
                    "semester": int(row["semester"]) if row.get("semester") else 1,
                }
                CourseCreate.model_validate(data)
                if not code or not data["title"] or not data["department"]:
                    raise ValueError("Course code, title, and department are required")
                if db.scalar(select(Course.id).where(
                    Course.tenant_id == ctx.tenant.id, Course.course_code == code,
                )):
                    raise ValueError(f"Course code '{code}' already exists in this college")
                key = (kind, code.casefold())
                normalized.append((line, data))
            elif kind == "attendance":
                course_code = (row.get("course_code") or "").upper()
                student_reg = row.get("student_id") or row.get("registration_number") or ""
                attendance_date = date.fromisoformat(row.get("date", ""))
                record_status = (row.get("status") or "present").lower()
                if record_status not in {"present", "absent", "late"}:
                    raise ValueError("Status must be present, absent, or late")
                course = db.scalar(select(Course).where(Course.tenant_id == ctx.tenant.id, Course.course_code == course_code))
                student = db.scalar(select(Student).where(Student.tenant_id == ctx.tenant.id, Student.registration_number == student_reg))
                if course is None or student is None:
                    raise ValueError("Course code and student ID must exist in this college")
                if ctx.role == "faculty" and db.scalar(select(CourseAssignment.id).where(
                    CourseAssignment.tenant_id == ctx.tenant.id,
                    CourseAssignment.course_id == course.id,
                    CourseAssignment.faculty_id == faculty.id,
                )) is None:
                    raise ValueError("Course is not assigned to your faculty account")
                if db.scalar(select(CourseEnrollment.id).where(
                    CourseEnrollment.tenant_id == ctx.tenant.id,
                    CourseEnrollment.course_id == course.id,
                    CourseEnrollment.student_id == student.id,
                )) is None:
                    raise ValueError("Student is not enrolled in this course")
                key = (kind, course.id, student.id, attendance_date)
                data = {"course_id": course.id, "course_code": course.course_code,
                        "student_id": student.id, "student_code": student.registration_number,
                        "date": attendance_date, "status": record_status,
                        "faculty_id": faculty.id if faculty else None}
                normalized.append((line, data))
            else:
                course_code = (row.get("course_code") or "").upper()
                exam_title = row.get("exam_title") or ""
                student_reg = row.get("student_id") or row.get("registration_number") or ""
                marks = float(row.get("marks_obtained", ""))
                course = db.scalar(select(Course).where(Course.tenant_id == ctx.tenant.id, Course.course_code == course_code))
                student = db.scalar(select(Student).where(Student.tenant_id == ctx.tenant.id, Student.registration_number == student_reg))
                exam = db.scalar(select(Examination).where(
                    Examination.tenant_id == ctx.tenant.id,
                    Examination.course_id == course.id if course else False,
                    Examination.title == exam_title,
                ))
                if course is None or student is None or exam is None:
                    raise ValueError("Course, examination title and student ID must exist in this college")
                if ctx.role == "faculty" and db.scalar(select(CourseAssignment.id).where(
                    CourseAssignment.tenant_id == ctx.tenant.id,
                    CourseAssignment.course_id == course.id,
                    CourseAssignment.faculty_id == faculty.id,
                )) is None:
                    raise ValueError("Course is not assigned to your faculty account")
                if db.scalar(select(CourseEnrollment.id).where(
                    CourseEnrollment.tenant_id == ctx.tenant.id,
                    CourseEnrollment.course_id == course.id,
                    CourseEnrollment.student_id == student.id,
                )) is None:
                    raise ValueError("Student is not enrolled in the examination course")
                if marks < 0 or marks > exam.max_marks:
                    raise ValueError(f"Marks must be between 0 and {exam.max_marks}")
                key = (kind, exam.id, student.id)
                data = {"course_id": course.id, "course_code": course.course_code,
                        "examination_id": exam.id, "exam_title": exam.title,
                        "student_id": student.id, "student_code": student.registration_number,
                        "marks_obtained": marks}
                normalized.append((line, data))
            if key in seen:
                normalized.pop()
                raise ValueError("Duplicate record in this CSV")
            seen.add(key)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            message = str(exc)
            if isinstance(exc, ValidationError):
                message = "; ".join(item["msg"] for item in exc.errors())
            errors.append({"row": line, "errors": [message]})
    return normalized, errors


@router.post("/{kind}/preview")
def preview_csv(kind: ImportKind, payload: CsvImportRequest,
                ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
                db: Session = Depends(get_db)) -> dict:
    rows, errors = _validate(kind, payload.csv_text, ctx, db)
    return {"valid_rows": [{"row": line, "data": data} for line, data in rows],
            "errors": errors, "valid_count": len(rows), "error_count": len(errors)}


@router.post("/{kind}/commit")
def commit_csv(kind: ImportKind, payload: CsvImportRequest,
               ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
               db: Session = Depends(get_db)) -> dict:
    rows, errors = _validate(kind, payload.csv_text, ctx, db)
    for _, row in rows:
        if kind == "students":
            db.add(Student(tenant_id=ctx.tenant.id, **row, status="active"))
        elif kind == "faculty":
            db.add(Faculty(tenant_id=ctx.tenant.id, **row, status="active"))
        elif kind == "courses":
            db.add(Course(tenant_id=ctx.tenant.id, **row, status="active"))
        elif kind == "attendance":
            existing = db.scalar(select(Attendance).where(
                Attendance.tenant_id == ctx.tenant.id, Attendance.course_id == row["course_id"],
                Attendance.student_id == row["student_id"], Attendance.date == row["date"],
            ))
            if existing:
                existing.status = row["status"]
                if row["faculty_id"]:
                    existing.faculty_id = row["faculty_id"]
            else:
                db.add(Attendance(tenant_id=ctx.tenant.id, **{key: row[key] for key in ("course_id","student_id","date","status","faculty_id")}))
        else:
            existing = db.scalar(select(ExamResult).where(
                ExamResult.tenant_id == ctx.tenant.id,
                ExamResult.examination_id == row["examination_id"],
                ExamResult.student_id == row["student_id"],
            ))
            percent = row["marks_obtained"]
            grade = "S" if percent >= 90 else "A" if percent >= 80 else "B" if percent >= 70 else "C" if percent >= 60 else "D" if percent >= 50 else "F"
            if existing:
                existing.marks_obtained, existing.grade = percent, grade
            else:
                db.add(ExamResult(tenant_id=ctx.tenant.id, examination_id=row["examination_id"],
                                  student_id=row["student_id"], marks_obtained=percent, grade=grade))
    db.commit()
    return {"created_or_updated": len(rows), "failed": len(errors), "errors": errors}
