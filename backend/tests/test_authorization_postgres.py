"""PostgreSQL API security integration tests, isolated in a temporary schema.

Set POSTGRES_TEST_DATABASE_URL to a disposable test database URL to run these.
The fixture creates and drops a randomly named schema and never touches public.
"""
from collections.abc import Iterator
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db import get_db
from app.main import app
from app.models import Base, Course, Document, Examination, Student, Tenant
from scripts import seed_demo


@pytest.fixture
def postgres_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set POSTGRES_TEST_DATABASE_URL to run isolated PostgreSQL security tests")

    schema = f"cc_security_{uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_demo, "SessionLocal", session_local)
    seed_demo.seed()

    def override_get_db() -> Iterator[Session]:
        with session_local() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "allow_dev_auth", True)
    monkeypatch.setattr(settings, "demo_auth_password", "CampusCloudDemo!2026")
    monkeypatch.setattr(settings, "demo_auth_secret", "postgres-security-test-signing-secret")
    try:
        with TestClient(app) as client:
            setattr(client, "test_session_local", session_local)
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _headers(client: TestClient, email: str, tenant_code: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/demo-login",
        json={"email": email, "password": "CampusCloudDemo!2026", "tenant_code": tenant_code},
    )
    assert response.status_code == 200
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
        "X-Tenant-Code": tenant_code,
    }


def test_postgres_authenticated_role_and_cross_tenant_integrity(postgres_client: TestClient) -> None:
    client = postgres_client
    student_headers = _headers(client, "abhishek.poudel@vit.edu", "VIT001")
    vit_admin_headers = _headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    faculty_headers = _headers(client, "rajesh.sundaram@vit.edu", "VIT001")

    with client.test_session_local() as db:  # type: ignore[attr-defined]
        vit_id = db.scalar(select(Tenant.id).where(Tenant.tenant_code == "VIT001"))
        own = db.scalar(select(Student).where(Student.tenant_id == vit_id, Student.email == "abhishek.poudel@vit.edu"))
        other = db.scalar(select(Student).where(Student.tenant_id == vit_id, Student.email == "vivek.kumar@vit.edu"))
        abc_student = db.scalar(select(Student).where(Student.email == "rohan.mehta@abc.edu"))
        assigned_course = db.scalar(select(Course).where(Course.tenant_id == vit_id, Course.course_code == "MCA501"))
        unassigned_course = db.scalar(select(Course).where(Course.tenant_id == vit_id, Course.course_code == "MCA502"))
        exam = db.scalar(select(Examination).where(Examination.tenant_id == vit_id, Examination.course_id == assigned_course.id))
        assert own and other and abc_student and assigned_course and unassigned_course and exam

    assert client.get("/api/v1/students/me", headers=student_headers).status_code == 200
    assert client.get(f"/api/v1/students/{other.id}", headers=student_headers).status_code == 404
    assert client.post(
        "/api/v1/students",
        headers=student_headers,
        json={"registration_number": "STUDENT-DENIED", "first_name": "No", "last_name": "Access", "email": "no@example.test"},
    ).status_code == 403
    assert client.get("/api/v1/students", headers={**vit_admin_headers, "X-Tenant-Code": "ABC001"}).status_code == 403
    assert client.get(f"/api/v1/attendance?course_id={unassigned_course.id}", headers=faculty_headers).status_code == 403
    assert client.post(
        "/api/v1/attendance/bulk",
        headers=vit_admin_headers,
        json={"course_id": assigned_course.id, "date": "2026-10-30", "records": [{"student_id": abc_student.id}]},
    ).status_code == 404
    assert client.post(
        f"/api/v1/examinations/{exam.id}/results",
        headers=vit_admin_headers,
        json={"student_id": abc_student.id, "marks_obtained": 80},
    ).status_code == 404


def test_postgres_document_scope_and_super_admin_boundary(postgres_client: TestClient) -> None:
    client = postgres_client
    student_headers = _headers(client, "abhishek.poudel@vit.edu", "VIT001")
    super_headers = _headers(client, "superadmin@campuscloud.test", "VIT001")
    college_headers = _headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        vit_id = db.scalar(select(Tenant.id).where(Tenant.tenant_code == "VIT001"))
        private_student = db.scalar(select(Student).where(Student.tenant_id == vit_id, Student.email == "vivek.kumar@vit.edu"))
        document = Document(
            tenant_id=vit_id,
            uploaded_by="vit-admin",
            object_key=f"tenants/{vit_id}/private",
            original_filename="private.pdf",
            content_type="application/pdf",
            file_size=64,
            scope="student",
            student_id=private_student.id,
            status="active",
        )
        db.add(document)
        db.commit()
        document_id = document.id

    assert client.get(f"/api/v1/storage/presigned-download?document_id={document_id}", headers=student_headers).status_code == 404
    assert client.get("/api/v1/reports/multi-tenant-summary", headers={**super_headers, "X-Tenant-Code": "ABC001"}).status_code == 200
    assert client.get("/api/v1/reports/multi-tenant-summary", headers=college_headers).status_code == 403
