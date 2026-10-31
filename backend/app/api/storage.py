from datetime import datetime, timezone
from uuid import uuid4

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.models import Course, CourseAssignment, CourseEnrollment, Document, Faculty, Student
from app.security.identity_records import current_faculty, current_student
from app.security.tenant_context import TenantContext, require_roles
from app.services.object_storage import (
    ALLOWED_CONTENT_TYPES,
    create_download_url,
    create_s3_client,
    create_upload_form,
    verify_uploaded_object,
)

router = APIRouter(prefix="/storage", tags=["storage"])


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    file_size: int = Field(gt=0)
    scope: str = "college"
    category: str = "other"
    course_id: str | None = None
    student_id: str | None = None
    faculty_id: str | None = None

    @field_validator("original_filename")
    @classmethod
    def normalize_filename(cls, value: str) -> str:
        # Keep a display name only. Never let client paths become S3 keys.
        name = value.replace("\\", "/").split("/")[-1].strip()
        if not name or name in {".", ".."}:
            raise ValueError("A valid filename is required")
        return name

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        if value not in ALLOWED_CONTENT_TYPES:
            raise ValueError("Unsupported document content type")
        return value

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        if value not in {"college", "course", "student", "faculty"}:
            raise ValueError("Scope must be college, course, student, or faculty")
        return value

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in {"identity_proof", "marksheet", "certificate", "admission", "faculty", "other"}:
            raise ValueError("Unsupported document category")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> "UploadRequest":
        if self.scope == "college" and (self.course_id or self.student_id or self.faculty_id):
            raise ValueError("College documents cannot target a course or student")
        if self.scope == "course" and (not self.course_id or self.student_id or self.faculty_id):
            raise ValueError("Course documents require only a course ID")
        if self.scope == "student" and (self.course_id or self.faculty_id):
            raise ValueError("Student documents cannot target a course or faculty member")
        if self.scope == "faculty" and (not self.faculty_id or self.course_id or self.student_id):
            raise ValueError("Faculty documents require only a faculty ID")
        return self


class DocumentView(BaseModel):
    id: str
    original_filename: str
    content_type: str
    file_size: int
    scope: str
    category: str
    course_id: str | None
    student_id: str | None
    faculty_id: str | None
    uploaded_at: str
    status: str
    can_delete: bool = False


def _view(document: Document) -> dict:
    return {
        "id": document.id,
        "original_filename": document.original_filename,
        "content_type": document.content_type,
        "file_size": document.file_size,
        "scope": document.scope,
        "category": document.category,
        "course_id": document.course_id,
        "student_id": document.student_id,
        "faculty_id": document.faculty_id,
        "uploaded_at": document.uploaded_at.isoformat(),
        "status": document.status,
        "can_delete": False,
    }


def _can_read_document(document: Document, ctx: TenantContext, db: Session) -> bool:
    if ctx.role == "college_admin":
        return True
    if document.scope == "college":
        return ctx.role in {"faculty", "student"}
    if ctx.role == "faculty":
        faculty = current_faculty(ctx, db)
        if document.scope == "faculty":
            return document.faculty_id == faculty.id
        if document.scope == "course" and document.course_id:
            return db.scalar(
                select(CourseAssignment.id).where(
                    CourseAssignment.tenant_id == ctx.tenant.id,
                    CourseAssignment.course_id == document.course_id,
                    CourseAssignment.faculty_id == faculty.id,
                )
            ) is not None
        return False
    if ctx.role == "student":
        student = current_student(ctx, db)
        if document.scope == "student":
            return document.student_id == student.id
        if document.scope == "course" and document.course_id:
            return db.scalar(
                select(CourseEnrollment.id).where(
                    CourseEnrollment.tenant_id == ctx.tenant.id,
                    CourseEnrollment.course_id == document.course_id,
                    CourseEnrollment.student_id == student.id,
                )
            ) is not None
    return False


@router.post("/presigned-upload", status_code=status.HTTP_201_CREATED)
def request_presigned_upload(
    payload: UploadRequest,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> dict:
    """Create tenant metadata and an exact-size, tenant-prefix S3 upload form."""
    if payload.file_size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Document exceeds the upload size limit")
    if ctx.role == "faculty" and payload.scope != "course":
        raise HTTPException(status_code=403, detail="Faculty may upload documents only for courses they teach")
    student_target_id = payload.student_id
    if ctx.role == "student":
        if payload.scope != "student":
            raise HTTPException(status_code=403, detail="Students may upload only documents for their own account")
        if payload.category not in {"identity_proof", "marksheet", "certificate", "admission", "other"}:
            raise HTTPException(status_code=403, detail="This document category is not available for student uploads")
        actor = current_student(ctx, db)
        if payload.student_id and payload.student_id != actor.id:
            raise HTTPException(status_code=403, detail="Students may upload documents only to their own account")
        student_target_id = actor.id
    if payload.scope == "course":
        target = db.scalar(select(Course).where(
            Course.id == payload.course_id, Course.tenant_id == ctx.tenant.id,
        ))
        if target is None:
            raise HTTPException(status_code=404, detail="Course not found in this tenant")
        if ctx.role == "faculty":
            actor = current_faculty(ctx, db)
            if db.scalar(select(CourseAssignment.id).where(
                CourseAssignment.tenant_id == ctx.tenant.id,
                CourseAssignment.course_id == target.id,
                CourseAssignment.faculty_id == actor.id,
            )) is None:
                raise HTTPException(status_code=403, detail="Course is not assigned to this faculty member")
    if payload.scope == "student" and ctx.role != "student":
        target = db.scalar(select(Student).where(
            Student.id == payload.student_id, Student.tenant_id == ctx.tenant.id,
        ))
        if target is None:
            raise HTTPException(status_code=404, detail="Student not found in this tenant")
    if payload.scope == "faculty":
        target = db.scalar(select(Faculty).where(
            Faculty.id == payload.faculty_id, Faculty.tenant_id == ctx.tenant.id,
        ))
        if target is None:
            raise HTTPException(status_code=404, detail="Faculty member not found in this tenant")

    document_id = str(uuid4())
    object_key = f"tenants/{ctx.tenant.id}/{document_id}"
    try:
        upload = create_upload_form(
            ctx.tenant.id,
            payload.content_type,
            object_key=object_key,
            exact_size=payload.file_size,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Document upload could not be prepared") from exc

    document = Document(
        id=document_id,
        tenant_id=ctx.tenant.id,
        uploaded_by=ctx.principal.subject,
        object_key=object_key,
        original_filename=payload.original_filename,
        content_type=payload.content_type,
        file_size=payload.file_size,
        scope=payload.scope,
        category=payload.category,
        course_id=payload.course_id,
        student_id=student_target_id,
        faculty_id=payload.faculty_id,
        status="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return {"document": _view(document), "upload": upload}


@router.post("/documents/{document_id}/complete", response_model=DocumentView)
def complete_upload(
    document_id: str,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> dict:
    document = db.scalar(select(Document).where(
        Document.id == document_id,
        Document.tenant_id == ctx.tenant.id,
    ))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.uploaded_by != ctx.principal.subject:
        raise HTTPException(status_code=403, detail="Only the uploading administrator can complete this upload")
    if ctx.role == "faculty" and (document.scope != "course" or not _can_read_document(document, ctx, db)):
        raise HTTPException(status_code=403, detail="Faculty may complete uploads only for assigned courses")
    if ctx.role == "student" and (document.scope != "student" or not _can_read_document(document, ctx, db)):
        raise HTTPException(status_code=403, detail="Students may complete uploads only to their own account")
    if document.status != "active":
        try:
            verify_uploaded_object(document.object_key, document.content_type, document.file_size)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Document upload could not be verified") from exc
        document.status = "active"
        document.uploaded_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(document)
    return _view(document)


@router.get("/documents", response_model=list[DocumentView])
def list_documents(
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> list[dict]:
    documents = db.scalars(select(Document).where(
        Document.tenant_id == ctx.tenant.id,
        Document.status == "active",
    ).order_by(Document.uploaded_at.desc())).all()
    visible = []
    for document in documents:
        if not _can_read_document(document, ctx, db):
            continue
        view = _view(document)
        view["can_delete"] = ctx.role == "college_admin" or (
            ctx.role == "faculty"
            and document.uploaded_by == ctx.principal.subject
            and document.scope == "course"
        )
        visible.append(view)
    return visible


@router.get("/presigned-download")
def request_presigned_download(
    document_id: str = Query(..., description="Database document ID; S3 keys are never accepted from callers"),
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty", "student")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    document = db.scalar(select(Document).where(
        Document.id == document_id,
        Document.tenant_id == ctx.tenant.id,
        Document.status == "active",
    ))
    if document is None or not _can_read_document(document, ctx, db):
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        url = create_download_url(ctx.tenant.id, document.object_key)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Document download could not be prepared") from exc
    return {"download_url": url}


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    ctx: TenantContext = Depends(require_roles("college_admin", "faculty")),
    db: Session = Depends(get_db),
) -> None:
    document = db.scalar(select(Document).where(
        Document.id == document_id,
        Document.tenant_id == ctx.tenant.id,
    ))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if ctx.role == "faculty" and (
        document.uploaded_by != ctx.principal.subject
        or document.scope != "course"
        or not _can_read_document(document, ctx, db)
    ):
        raise HTTPException(status_code=403, detail="Faculty may remove only their own assigned-course documents")
    try:
        create_s3_client().delete_object(Bucket=settings.s3_bucket, Key=document.object_key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Document could not be removed from storage") from exc
    db.delete(document)
    db.commit()
