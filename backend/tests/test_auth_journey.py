from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import func, select

from app.core.config import settings
from app.db import get_db
from app.main import app
from app.models import (
    Attendance,
    Base,
    Course,
    CourseAssignment,
    CourseEnrollment,
    Document,
    ExamResult,
    Examination,
    Faculty,
    LocalAccount,
    Membership,
    Student,
    Tenant,
)
from app.security.cognito import DEMO_TOKEN_ISSUER, jwt
from scripts import seed_demo


@pytest.fixture
def client_with_seeded_demo(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_demo, "SessionLocal", test_session_local)
    seed_demo.seed()
    seed_demo.seed()

    def override_get_db() -> Iterator[Session]:
        with test_session_local() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "allow_dev_auth", True)
    monkeypatch.setattr(settings, "demo_auth_password", "CampusCloudDemo!2026")
    monkeypatch.setattr(settings, "demo_auth_secret", "local-demo-signing-secret-for-tests")
    try:
        with TestClient(app) as client:
            setattr(client, "test_session_local", test_session_local)
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_demo_campus_login_and_tenant_isolation(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo

    campuses = client.get("/api/v1/tenants/public")
    assert campuses.status_code == 200
    assert {item["tenant_code"] for item in campuses.json()} == {"VIT001", "ABC001", "XYZ001"}

    login = client.post(
        "/api/v1/auth/demo-login",
        json={
            "email": "college.admin@vit.campuscloud.test",
            "password": "CampusCloudDemo!2026",
            "tenant_code": "VIT001",
        },
    )
    assert login.status_code == 200
    credentials = login.json()
    assert credentials["role"] == "college_admin"
    assert credentials["tenant"]["tenant_code"] == "VIT001"

    headers = {
        "Authorization": f"Bearer {credentials['access_token']}",
        "X-Tenant-Code": "VIT001",
    }
    memberships = client.get("/api/v1/me/memberships", headers=headers)
    assert memberships.status_code == 200
    assert memberships.json()[0]["role"] == "college_admin"

    own_campus_report = client.get("/api/v1/reports/analytics", headers=headers)
    assert own_campus_report.status_code == 200
    assert own_campus_report.json()["tenant_code"] == "VIT001"

    other_campus_report = client.get(
        "/api/v1/reports/analytics",
        headers={**headers, "X-Tenant-Code": "ABC001"},
    )
    assert other_campus_report.status_code == 403

    unauthorized_campus_login = client.post(
        "/api/v1/auth/demo-login",
        json={
            "email": "college.admin@vit.campuscloud.test",
            "password": "CampusCloudDemo!2026",
            "tenant_code": "ABC001",
        },
    )
    assert unauthorized_campus_login.status_code == 401

    role_cases = [
        ("rajesh.sundaram@vit.edu", "VIT001", "faculty"),
        ("abhishek.poudel@vit.edu", "VIT001", "student"),
        ("superadmin@campuscloud.test", "ABC001", "platform_admin"),
    ]
    for email, tenant_code, expected_role in role_cases:
        response = client.post(
            "/api/v1/auth/demo-login",
            json={
                "email": email,
                "password": "CampusCloudDemo!2026",
                "tenant_code": tenant_code,
            },
        )
        assert response.status_code == 200
        assert response.json()["role"] == expected_role
        if expected_role == "platform_admin":
            summary = client.get(
                "/api/v1/reports/multi-tenant-summary",
                headers={
                    "Authorization": f"Bearer {response.json()['access_token']}",
                    "X-Tenant-Code": tenant_code,
                },
            )
            assert summary.status_code == 200
            assert {item["tenant_code"] for item in summary.json()} == {"VIT001", "ABC001", "XYZ001"}


def test_admin_record_updates_are_tenant_scoped(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    vit = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    abc = _demo_headers(client, "college.admin@abc.campuscloud.test", "ABC001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        student = db.scalar(select(Student).where(Student.registration_number == "26MCA0180"))
        faculty = db.scalar(select(Faculty).where(Faculty.employee_id == "FAC-VIT-01"))
        course = db.scalar(select(Course).where(Course.course_code == "MCA501"))
        student_id, faculty_id, course_id = student.id, faculty.id, course.id
    assert client.patch(f"/api/v1/students/{student_id}", headers=vit, json={"semester": 2}).status_code == 200
    assert client.patch(f"/api/v1/faculty/{faculty_id}", headers=vit, json={"designation": "Professor"}).status_code == 200
    assert client.patch(f"/api/v1/courses/{course_id}", headers=vit, json={"credits": 4}).status_code == 200
    assert client.patch(f"/api/v1/students/{student_id}", headers=abc, json={"semester": 3}).status_code == 404
    assert client.patch(f"/api/v1/faculty/{faculty_id}", headers=abc, json={"designation": "Professor"}).status_code == 404
    assert client.patch(f"/api/v1/courses/{course_id}", headers=abc, json={"credits": 4}).status_code == 404


def test_runtime_info_reports_live_dependency_state_without_secrets(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    admin = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    response = client.get("/info")
    assert response.status_code == 200
    data = response.json()
    assert data["database_type"] in {"PostgreSQL", "sqlite"}
    assert data["database_connectivity"] == "connected"
    assert data["s3_connectivity"] in {"not_configured", "unavailable"}
    assert "bucket" not in data and "password" not in data


def test_seeded_academic_journey_is_complete_and_repeatable(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    models = (Student, Faculty, Course, CourseAssignment, CourseEnrollment, Attendance, Examination, ExamResult)

    def snapshot() -> dict[tuple[str, str], int]:
        with client.test_session_local() as db:  # type: ignore[attr-defined]
            tenants = db.scalars(select(Tenant)).all()
            return {
                (tenant.tenant_code, model.__tablename__): db.scalar(
                    select(func.count()).select_from(model).where(model.tenant_id == tenant.id)
                ) or 0
                for tenant in tenants for model in models
            }

    before = snapshot()
    seed_demo.seed()
    after = snapshot()
    assert after == before
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        for tenant in db.scalars(select(Tenant)):
            students = db.scalars(select(Student).where(Student.tenant_id == tenant.id)).all()
            assert students
            for student in students:
                enrollments = db.scalars(select(CourseEnrollment).where(
                    CourseEnrollment.tenant_id == tenant.id, CourseEnrollment.student_id == student.id,
                )).all()
                assert enrollments
                assert db.scalar(select(Attendance.id).where(
                    Attendance.tenant_id == tenant.id, Attendance.student_id == student.id,
                ))
                assert db.scalar(select(ExamResult.id).where(
                    ExamResult.tenant_id == tenant.id, ExamResult.student_id == student.id,
                ))
            assert db.scalar(select(Examination.id).where(
                Examination.tenant_id == tenant.id, Examination.date >= date(2026, 11, 1),
            ))


def test_seeded_student_can_view_scoped_academic_data(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    headers = _demo_headers(client, "abhishek.poudel@vit.edu", "VIT001")
    courses = client.get("/api/v1/courses", headers=headers)
    attendance = client.get("/api/v1/attendance", headers=headers)
    examinations = client.get("/api/v1/examinations", headers=headers)
    documents = client.get("/api/v1/storage/documents", headers=headers)
    assert courses.status_code == attendance.status_code == examinations.status_code == documents.status_code == 200
    assert courses.json() and all(item["faculty_names"] for item in courses.json())
    assert attendance.json()
    assert examinations.json() and any(exam["results"] for exam in examinations.json())
    assert documents.json() == []  # No fake S3 objects or metadata are seeded.
    assert client.get("/api/v1/courses", headers={**headers, "X-Tenant-Code": "ABC001"}).status_code == 403


def test_demo_login_rejects_bad_password_and_is_disabled_outside_development(
    client_with_seeded_demo: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "email": "college.admin@vit.campuscloud.test",
        "password": "wrong-password",
        "tenant_code": "VIT001",
    }
    assert client_with_seeded_demo.post("/api/v1/auth/demo-login", json=payload).status_code == 401

    monkeypatch.setattr(settings, "app_env", "production")
    payload["password"] = "CampusCloudDemo!2026"
    assert client_with_seeded_demo.post("/api/v1/auth/demo-login", json=payload).status_code == 404


def test_student_registration_login_tenant_scope_and_duplicate_rejection(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    vit_payload = {
        "full_name": "New VIT Student",
        "email": "new.student@example.test",
        "password": "StudentPass!2026",
        "confirm_password": "StudentPass!2026",
        "tenant_code": "VIT001",
    }
    vit_registration = client.post("/api/v1/auth/register", json=vit_payload)
    assert vit_registration.status_code == 201
    vit_credentials = vit_registration.json()
    assert vit_credentials["role"] == "student"
    assert vit_credentials["tenant"]["tenant_code"] == "VIT001"

    vit_login = client.post(
        "/api/v1/auth/demo-login",
        json={"email": vit_payload["email"], "password": vit_payload["password"], "tenant_code": "VIT001"},
    )
    assert vit_login.status_code == 200
    assert vit_login.json()["role"] == "student"
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        vit_tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == "VIT001"))
        account = db.scalar(
            select(LocalAccount).where(
                LocalAccount.tenant_id == vit_tenant.id,
                LocalAccount.email == vit_payload["email"],
            )
        )
        assert account is not None
        assert account.password_hash != vit_payload["password"]
        assert account.password_hash.startswith("scrypt$")

    duplicate = client.post("/api/v1/auth/register", json=vit_payload)
    assert duplicate.status_code == 409

    abc_payload = {**vit_payload, "email": "new.abc.student@example.test", "tenant_code": "ABC001"}
    abc_registration = client.post("/api/v1/auth/register", json=abc_payload)
    assert abc_registration.status_code == 201
    abc_login = client.post(
        "/api/v1/auth/demo-login",
        json={"email": abc_payload["email"], "password": abc_payload["password"], "tenant_code": "ABC001"},
    )
    assert abc_login.status_code == 200
    assert abc_login.json()["tenant"]["tenant_code"] == "ABC001"

    vit_headers = {
        "Authorization": f"Bearer {vit_login.json()['access_token']}",
        "X-Tenant-Code": "VIT001",
    }
    own_memberships = client.get("/api/v1/me/memberships", headers=vit_headers)
    assert own_memberships.status_code == 200
    assert [item["tenant_code"] for item in own_memberships.json()] == ["VIT001"]
    other_tenant_data = client.get(
        "/api/v1/reports/analytics",
        headers={**vit_headers, "X-Tenant-Code": "ABC001"},
    )
    assert other_tenant_data.status_code == 403

    wrong_campus_login = client.post(
        "/api/v1/auth/demo-login",
        json={"email": vit_payload["email"], "password": vit_payload["password"], "tenant_code": "ABC001"},
    )
    assert wrong_campus_login.status_code == 401


def test_admin_provisioned_student_can_claim_local_account_and_view_academics(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    admin = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    student = client.post("/api/v1/students", headers=admin, json={
        "registration_number": "VIT-CLAIM-01", "first_name": "Taylor", "last_name": "Claim",
        "email": "taylor.claim@example.test", "department": "MCA", "semester": 1,
    })
    assert student.status_code == 201
    student_id = student.json()["id"]

    course = client.post("/api/v1/courses", headers=admin, json={
        "course_code": "MCA-CLAIM", "title": "Claimed Student Course", "department": "MCA",
        "credits": 3, "semester": 1,
    })
    assert course.status_code == 201
    course_id = course.json()["id"]
    assert client.post(f"/api/v1/courses/{course_id}/students", headers=admin,
                       json={"student_id": student_id}).status_code == 201
    assert client.post("/api/v1/attendance/bulk", headers=admin, json={
        "course_id": course_id, "date": "2026-10-30",
        "records": [{"student_id": student_id, "status": "present"}],
    }).status_code == 201
    exam = client.post("/api/v1/examinations", headers=admin, json={
        "course_id": course_id, "title": "Claimed Student Exam", "exam_type": "quiz",
        "max_marks": 20, "date": "2026-10-30",
    })
    assert exam.status_code == 201
    assert client.post(f"/api/v1/examinations/{exam.json()['id']}/results", headers=admin, json={
        "student_id": student_id, "marks_obtained": 18,
    }).status_code == 201

    registration = client.post("/api/v1/auth/register", json={
        "full_name": "Taylor Claim", "email": "taylor.claim@example.test",
        "password": "StudentPass!2026", "confirm_password": "StudentPass!2026", "tenant_code": "VIT001",
    })
    assert registration.status_code == 201
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}", "X-Tenant-Code": "VIT001"}
    assert client.get("/api/v1/students/me", headers=headers).json()["id"] == student_id
    assert any(row["id"] == course_id for row in client.get("/api/v1/courses", headers=headers).json())
    assert any(row["course_id"] == course_id for row in client.get("/api/v1/attendance", headers=headers).json())
    exams = client.get("/api/v1/examinations", headers=headers).json()
    assert any(row["id"] == exam.json()["id"] and row["results"][0]["marks_obtained"] == 18 for row in exams)
    assert client.post("/api/v1/auth/register", json={
        "full_name": "Taylor Claim", "email": "taylor.claim@example.test",
        "password": "StudentPass!2026", "confirm_password": "StudentPass!2026", "tenant_code": "VIT001",
    }).status_code == 409


def test_registration_rejects_unknown_tenant_and_privileged_roles(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    payload = {
        "full_name": "New Student",
        "email": "role.test@example.test",
        "password": "StudentPass!2026",
        "confirm_password": "StudentPass!2026",
        "tenant_code": "VIT001",
    }
    invalid_campus = client.post("/api/v1/auth/register", json={**payload, "tenant_code": "NOPE999"})
    assert invalid_campus.status_code == 404

    for role in ("college_admin", "platform_admin", "super_admin"):
        response = client.post("/api/v1/auth/register", json={**payload, "role": role})
        assert response.status_code == 403

    invalid_confirmation = client.post(
        "/api/v1/auth/register",
        json={**payload, "confirm_password": "DifferentPass!2026"},
    )
    assert invalid_confirmation.status_code == 422

    # The seeded accounts continue to authenticate with the project demo password.
    seeded = client.post(
        "/api/v1/auth/demo-login",
        json={
            "email": "college.admin@vit.campuscloud.test",
            "password": "CampusCloudDemo!2026",
            "tenant_code": "VIT001",
        },
    )
    assert seeded.status_code == 200
    assert seeded.json()["role"] == "college_admin"


def test_cognito_registration_confirmation_and_student_membership(
    client_with_seeded_demo: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import auth as auth_api

    class CognitoStub:
        def sign_up(self, **kwargs: object) -> dict[str, object]:
            assert "ClientId" in kwargs
            assert "Password" in kwargs
            return {
                "UserSub": "cognito-student-sub",
                "UserConfirmed": False,
                "CodeDeliveryDetails": {"DeliveryMedium": "EMAIL"},
            }

        def confirm_sign_up(self, **kwargs: object) -> None:
            assert kwargs["ConfirmationCode"] == "123456"

    cognito = CognitoStub()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_dev_auth", False)
    monkeypatch.setattr(settings, "aws_region", "ap-south-1")
    monkeypatch.setattr(settings, "cognito_app_client_id", "public-test-client")
    monkeypatch.setattr(auth_api, "cognito_client", lambda: cognito)
    monkeypatch.setattr(
        auth_api,
        "decode_cognito_id_token",
        lambda token: {
            "sub": "cognito-student-sub",
            "email": "cognito.student@example.test",
            "email_verified": True,
            "token_use": "id",
        },
    )

    registration = client_with_seeded_demo.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Cognito Student",
            "email": "cognito.student@example.test",
            "password": "CognitoPass!2026",
            "confirm_password": "CognitoPass!2026",
            "tenant_code": "VIT001",
        },
    )
    assert registration.status_code == 201
    assert registration.json()["status"] == "confirmation_required"
    assert "access_token" not in registration.json()

    confirmation = client_with_seeded_demo.post(
        "/api/v1/auth/confirm-registration",
        json={"email": "cognito.student@example.test", "confirmation_code": "123456"},
    )
    assert confirmation.status_code == 200

    completed = client_with_seeded_demo.post(
        "/api/v1/auth/complete-registration",
        json={"id_token": "mock-id-token-with-server-verified-claims"},
    )
    assert completed.status_code == 200
    assert completed.json() == {"completed": True}
    with client_with_seeded_demo.test_session_local() as db:  # type: ignore[attr-defined]
        tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == "VIT001"))
        membership = db.scalar(
            select(Membership).where(
                Membership.subject == "cognito-student-sub",
                Membership.tenant_id == tenant.id,
            )
        )
        assert membership is not None
        assert membership.role == "student"


def _demo_headers(client: TestClient, email: str, tenant_code: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/demo-login",
        json={"email": email, "password": "CampusCloudDemo!2026", "tenant_code": tenant_code},
    )
    assert response.status_code == 200
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
        "X-Tenant-Code": tenant_code,
    }


def test_students_are_limited_to_their_own_record_and_cannot_administer(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    headers = _demo_headers(client, "abhishek.poudel@vit.edu", "VIT001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        own = db.scalar(select(Student).where(Student.tenant_id == select(Tenant.id).where(Tenant.tenant_code == "VIT001").scalar_subquery(), Student.email == "abhishek.poudel@vit.edu"))
        other = db.scalar(select(Student).where(Student.tenant_id == own.tenant_id, Student.email == "vivek.kumar@vit.edu"))
        assert own is not None and other is not None

    own_profile = client.get("/api/v1/students/me", headers=headers)
    assert own_profile.status_code == 200
    assert client.get("/api/v1/students/me", headers=headers).json()["id"] == own.id
    assert client.get(f"/api/v1/students/{other.id}", headers=headers).status_code == 404
    assert client.get("/api/v1/students", headers=headers).status_code == 403
    student_courses = client.get("/api/v1/courses", headers=headers)
    assert student_courses.status_code == 200
    assert {item["course_code"] for item in student_courses.json()} == {"MCA501", "MCA502", "MCA503"}
    own_attendance = client.get("/api/v1/attendance", headers=headers)
    assert own_attendance.status_code == 200
    assert all(item["student_id"] == own.id for item in own_attendance.json())
    own_fees = client.get("/api/v1/fees", headers=headers)
    assert own_fees.status_code == 200
    assert all(item["student_id"] == own.id for item in own_fees.json())
    own_exams = client.get("/api/v1/examinations", headers=headers)
    assert own_exams.status_code == 200
    assert all(
        result["student_id"] == own.id
        for exam in own_exams.json()
        for result in exam["results"]
    )
    assert client.post(
        "/api/v1/students",
        headers=headers,
        json={
            "registration_number": "STUDENT-ADMIN-ATTEMPT",
            "first_name": "Attempt",
            "last_name": "Denied",
            "email": "attempt@example.test",
        },
    ).status_code == 403
    assert client.get("/api/v1/fees", headers={**headers, "X-Tenant-Code": "ABC001"}).status_code == 403


def test_faculty_cannot_access_unassigned_course_or_write_admin_records(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    headers = _demo_headers(client, "rajesh.sundaram@vit.edu", "VIT001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        vit_id = db.scalar(select(Tenant.id).where(Tenant.tenant_code == "VIT001"))
        course = db.scalar(select(Course).where(Course.tenant_id == vit_id, Course.course_code == "MCA502"))
        assigned_course = db.scalar(select(Course).where(Course.tenant_id == vit_id, Course.course_code == "MCA501"))
        student = db.scalar(select(Student).where(Student.tenant_id == vit_id, Student.email == "abhishek.poudel@vit.edu"))
        other_faculty = db.scalar(select(Faculty).where(Faculty.tenant_id == vit_id, Faculty.email == "priya.natarajan@vit.edu"))
        assert course is not None and assigned_course is not None and student is not None and other_faculty is not None
    assert client.get(f"/api/v1/courses?semester=1", headers=headers).status_code == 200
    assert all(item["course_code"] != "MCA502" for item in client.get("/api/v1/courses", headers=headers).json())
    assert client.get(f"/api/v1/attendance?course_id={course.id}", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/attendance/bulk",
        headers=headers,
        json={"course_id": course.id, "date": "2026-10-30", "records": [{"student_id": student.id}]},
    ).status_code == 403
    assert client.post(
        "/api/v1/attendance/bulk",
        headers=headers,
        json={
            "course_id": assigned_course.id,
            "date": "2026-10-30",
            "faculty_id": other_faculty.id,
            "records": [{"student_id": student.id}],
        },
    ).status_code == 403
    assert client.post(
        "/api/v1/faculty",
        headers=headers,
        json={"employee_id": "FORBIDDEN", "first_name": "A", "last_name": "B", "email": "a@b.test", "department": "MCA"},
    ).status_code == 403


def test_college_admin_cannot_select_another_tenant_or_spoof_tenant_id(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    headers = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    assert client.get("/api/v1/students", headers={**headers, "X-Tenant-Code": "ABC001"}).status_code == 403
    spoofed = client.post(
        "/api/v1/students",
        headers=headers,
        json={
            "tenant_id": "attacker-selected-tenant",
            "registration_number": "VIT-SPOOF",
            "first_name": "Wrong",
            "last_name": "Tenant",
            "email": "spoof@example.test",
        },
    )
    assert spoofed.status_code == 422


def test_cross_tenant_attendance_and_exam_references_are_rejected(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    headers = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        vit_id = db.scalar(select(Tenant.id).where(Tenant.tenant_code == "VIT001"))
        abc_student = db.scalar(select(Student).where(Student.tenant_id == select(Tenant.id).where(Tenant.tenant_code == "ABC001").scalar_subquery()))
        vit_course = db.scalar(select(Course).where(Course.tenant_id == vit_id, Course.course_code == "MCA501"))
        vit_exam = db.scalar(select(Examination).where(Examination.tenant_id == vit_id, Examination.course_id == vit_course.id))
        assert abc_student is not None and vit_course is not None and vit_exam is not None
        abc_faculty = db.scalar(select(Faculty).where(
            Faculty.tenant_id == select(Tenant.id).where(Tenant.tenant_code == "ABC001").scalar_subquery(),
        ))
        assert abc_faculty is not None

    attendance = client.post(
        "/api/v1/attendance/bulk",
        headers=headers,
        json={"course_id": vit_course.id, "date": "2026-10-30", "records": [{"student_id": abc_student.id}]},
    )
    assert attendance.status_code == 404
    result = client.post(
        f"/api/v1/examinations/{vit_exam.id}/results",
        headers=headers,
        json={"student_id": abc_student.id, "marks_obtained": 80},
    )
    assert result.status_code == 404
    assignment = client.post(
        f"/api/v1/courses/{vit_course.id}/faculty",
        headers=headers,
        json={"faculty_id": abc_faculty.id},
    )
    assert assignment.status_code == 404
    enrollment = client.post(
        f"/api/v1/courses/{vit_course.id}/students",
        headers=headers,
        json={"student_id": abc_student.id},
    )
    assert enrollment.status_code == 404


def test_document_download_requires_authorized_tenant_and_document_scope(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    student_headers = _demo_headers(client, "abhishek.poudel@vit.edu", "VIT001")
    vit_admin_headers = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    abc_admin_headers = _demo_headers(client, "college.admin@abc.campuscloud.test", "ABC001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        vit_id = db.scalar(select(Tenant.id).where(Tenant.tenant_code == "VIT001"))
        own = db.scalar(select(Student).where(Student.tenant_id == vit_id, Student.email == "abhishek.poudel@vit.edu"))
        other = db.scalar(select(Student).where(Student.tenant_id == vit_id, Student.email == "vivek.kumar@vit.edu"))
        doc = Document(
            tenant_id=vit_id,
            uploaded_by="college-admin",
            object_key=f"tenants/{vit_id}/private-document",
            original_filename="private.pdf",
            content_type="application/pdf",
            file_size=100,
            scope="student",
            student_id=other.id,
            status="active",
        )
        db.add(doc)
        db.commit()
        document_id = doc.id
    assert client.get(f"/api/v1/storage/presigned-download?document_id={document_id}", headers=student_headers).status_code == 404
    assert client.get(
        "/api/v1/storage/presigned-download?object_key=tenants/" + str(own.id) + "/guessed",
        headers=vit_admin_headers,
    ).status_code == 422
    assert client.get(f"/api/v1/storage/presigned-download?document_id={document_id}", headers={**student_headers, "X-Tenant-Code": "ABC001"}).status_code == 403
    assert client.get(f"/api/v1/storage/presigned-download?document_id={document_id}", headers=abc_admin_headers).status_code == 404


def test_document_upload_records_metadata_and_only_issues_url_after_authorization(
    client_with_seeded_demo: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import storage as storage_api

    client = client_with_seeded_demo
    admin_headers = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    student_headers = _demo_headers(client, "abhishek.poudel@vit.edu", "VIT001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == "VIT001"))
        student = db.scalar(select(Student).where(Student.tenant_id == tenant.id, Student.email == "abhishek.poudel@vit.edu"))
        assert student is not None
        student_id = student.id

    monkeypatch.setattr(storage_api, "create_upload_form", lambda tenant_id, content_type, **kwargs: {
        "url": "https://s3.test/upload", "fields": {"key": kwargs["object_key"]},
    })
    monkeypatch.setattr(storage_api, "verify_uploaded_object", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage_api, "create_download_url", lambda tenant_id, object_key: f"https://s3.test/{object_key}")
    class MockS3Client:
        def delete_object(self, **kwargs):
            return {"Deleted": True}
    monkeypatch.setattr(storage_api, "create_s3_client", lambda: MockS3Client())
    upload = client.post(
        "/api/v1/storage/presigned-upload",
        headers=admin_headers,
        json={
            "original_filename": "transcripts\\semester-one.pdf",
            "content_type": "application/pdf",
            "file_size": 256,
            "scope": "student",
            "category": "marksheet",
            "student_id": student_id,
        },
    )
    assert upload.status_code == 201
    doc = upload.json()["document"]
    assert doc["original_filename"] == "semester-one.pdf"
    assert doc["category"] == "marksheet"
    assert doc["status"] == "pending"
    assert upload.json()["upload"]["fields"]["key"].startswith("tenants/")

    document_id = doc["id"]
    assert client.get(
        f"/api/v1/storage/presigned-download?document_id={document_id}",
        headers=student_headers,
    ).status_code == 404
    complete = client.post(f"/api/v1/storage/documents/{document_id}/complete", headers=admin_headers)
    assert complete.status_code == 200
    assert complete.json()["status"] == "active"
    download = client.get(
        f"/api/v1/storage/presigned-download?document_id={document_id}",
        headers=student_headers,
    )
    assert download.status_code == 200
    assert download.json()["download_url"].startswith("https://s3.test/tenants/")


def test_faculty_document_upload_is_limited_to_assigned_course_and_student_scope(
    client_with_seeded_demo: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import storage as storage_api

    client = client_with_seeded_demo
    faculty_headers = _demo_headers(client, "rajesh.sundaram@vit.edu", "VIT001")
    student_headers = _demo_headers(client, "abhishek.poudel@vit.edu", "VIT001")
    other_faculty_headers = _demo_headers(client, "priya.natarajan@vit.edu", "VIT001")
    monkeypatch.setattr(storage_api, "create_upload_form", lambda tenant_id, content_type, **kwargs: {
        "url": "https://s3.test/upload", "fields": {"key": kwargs["object_key"]},
    })
    monkeypatch.setattr(storage_api, "verify_uploaded_object", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage_api, "create_download_url", lambda tenant_id, object_key: f"https://s3.test/{object_key}")
    class MockS3Client:
        def delete_object(self, **kwargs):
            return {"Deleted": True}
    monkeypatch.setattr(storage_api, "create_s3_client", lambda: MockS3Client())
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == "VIT001"))
        assigned = db.scalar(select(Course).where(Course.tenant_id == tenant.id, Course.course_code == "MCA501"))
        unassigned = db.scalar(select(Course).where(Course.tenant_id == tenant.id, Course.course_code == "MCA502"))
        assigned_id, unassigned_id = assigned.id, unassigned.id
    payload = {"original_filename":"course-guide.pdf","content_type":"application/pdf","file_size":128,
               "scope":"course","category":"certificate"}
    rejected = client.post("/api/v1/storage/presigned-upload", headers=faculty_headers,
                           json={**payload,"course_id":unassigned_id})
    assert rejected.status_code == 403
    upload = client.post("/api/v1/storage/presigned-upload", headers=faculty_headers,
                         json={**payload,"course_id":assigned_id})
    assert upload.status_code == 201
    document_id = upload.json()["document"]["id"]
    assert upload.json()["document"]["category"] == "certificate"
    assert client.post(f"/api/v1/storage/documents/{document_id}/complete", headers=faculty_headers).status_code == 200
    assert any(doc["id"] == document_id for doc in client.get("/api/v1/storage/documents", headers=student_headers).json())
    assert client.delete(f"/api/v1/storage/documents/{document_id}", headers=other_faculty_headers).status_code == 403
    assert client.delete(f"/api/v1/storage/documents/{document_id}", headers=faculty_headers).status_code == 204


def test_student_document_upload_is_bound_to_authenticated_student(
    client_with_seeded_demo: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import storage as storage_api

    client = client_with_seeded_demo
    student_headers = _demo_headers(client, "abhishek.poudel@vit.edu", "VIT001")
    other_student_headers = _demo_headers(client, "vivek.kumar@vit.edu", "VIT001")
    monkeypatch.setattr(storage_api, "create_upload_form", lambda tenant_id, content_type, **kwargs: {
        "url": "https://s3.test/upload", "fields": {"key": kwargs["object_key"]},
    })
    monkeypatch.setattr(storage_api, "verify_uploaded_object", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage_api, "create_download_url", lambda tenant_id, object_key: f"https://s3.test/{object_key}")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == "VIT001"))
        own = db.scalar(select(Student).where(Student.tenant_id == tenant.id, Student.email == "abhishek.poudel@vit.edu"))
        other = db.scalar(select(Student).where(Student.tenant_id == tenant.id, Student.email == "vivek.kumar@vit.edu"))
        own_id, other_id = own.id, other.id

    payload = {"original_filename":"identity.pdf","content_type":"application/pdf","file_size":128,
               "scope":"student","category":"identity_proof"}
    assert client.post("/api/v1/storage/presigned-upload", headers=student_headers,
                       json={**payload,"student_id":other_id}).status_code == 403
    assert client.post("/api/v1/storage/presigned-upload", headers=student_headers,
                       json={**payload,"scope":"course","course_id":"any-course"}).status_code == 403
    upload = client.post("/api/v1/storage/presigned-upload", headers=student_headers, json=payload)
    assert upload.status_code == 201
    document_id = upload.json()["document"]["id"]
    assert upload.json()["document"]["student_id"] == own_id
    assert client.post(f"/api/v1/storage/documents/{document_id}/complete", headers=student_headers).status_code == 200
    assert any(doc["id"] == document_id for doc in client.get("/api/v1/storage/documents", headers=student_headers).json())
    assert client.get(f"/api/v1/storage/presigned-download?document_id={document_id}", headers=other_student_headers).status_code == 404


def test_super_admin_summary_is_db_role_authorized_and_invalid_tenants_are_denied(
    client_with_seeded_demo: TestClient,
) -> None:
    client = client_with_seeded_demo
    super_headers = _demo_headers(client, "superadmin@campuscloud.test", "VIT001")
    college_headers = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    summary = client.get("/api/v1/reports/multi-tenant-summary", headers={**super_headers, "X-Tenant-Code": "ABC001"})
    assert summary.status_code == 200
    assert {item["tenant_code"] for item in summary.json()} == {"VIT001", "ABC001", "XYZ001"}
    assert client.get("/api/v1/reports/multi-tenant-summary", headers=college_headers).status_code == 403
    assert client.get("/api/v1/tenants", headers=college_headers).status_code == 403
    assert client.patch("/api/v1/tenants/XYZ001", headers=college_headers, json={"name": "forged"}).status_code == 403
    updated_tenant = client.patch("/api/v1/tenants/XYZ001", headers=super_headers, json={"name": "XYZ Institute Demo"})
    assert updated_tenant.status_code == 200
    assert updated_tenant.json()["name"] == "XYZ Institute Demo"
    assert client.get("/api/v1/students", headers={**college_headers, "X-Tenant-Code": "not-a-tenant-id"}).status_code == 403
    assert client.post("/api/v1/tenants", headers=college_headers,
                       json={"tenant_code": "NEW001", "name": "New College", "slug": "new-college"}).status_code == 403
    now = datetime.now(timezone.utc)
    forged_role = jwt.encode({
        "sub": "unprovisioned-user", "iss": DEMO_TOKEN_ISSUER, "iat": now,
        "exp": now + timedelta(minutes=10), "token_use": "access",
        "is_platform_admin": True, "role": "platform_admin",
    }, settings.demo_auth_secret, algorithm="HS256")
    forged = {"Authorization": f"Bearer {forged_role}", "X-Tenant-Code": "ABC001"}
    assert client.get("/api/v1/tenants", headers=forged).status_code == 403
    assert client.get("/api/v1/reports/multi-tenant-summary", headers=forged).status_code == 403


def test_admin_data_crud_phone_search_and_safe_deletes(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    headers = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    student = client.post("/api/v1/students", headers=headers, json={
        "registration_number":"CRUD-100","first_name":"Casey","last_name":"Student",
        "email":"casey.crud@example.test","phone":"+1 555 0100","department":"MCA","semester":1,
        "enrollment_date":"2026-09-01",
    })
    assert student.status_code == 201
    assert student.json()["phone"] == "+1 555 0100"
    assert len(client.get("/api/v1/students?search=CRUD-100", headers=headers).json()) == 1
    changed = client.patch(f"/api/v1/students/{student.json()['id']}", headers=headers, json={"phone":"+1 555 0101"})
    assert changed.status_code == 200 and changed.json()["phone"] == "+1 555 0101"
    assert client.delete(f"/api/v1/students/{student.json()['id']}", headers=headers).status_code == 204

    faculty = client.post("/api/v1/faculty", headers=headers, json={
        "employee_id":"CRUD-F1","first_name":"Morgan","last_name":"Teacher",
        "email":"morgan.crud@example.test","phone":"+1 555 0102","department":"MCA",
    })
    assert faculty.status_code == 201 and faculty.json()["phone"] == "+1 555 0102"
    assert client.get("/api/v1/faculty?search=CRUD-F1", headers=headers).status_code == 200
    assert client.delete(f"/api/v1/faculty/{faculty.json()['id']}", headers=headers).status_code == 204


def test_course_roster_and_enrollment_removal_are_tenant_scoped(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    vit_admin = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    abc_admin = _demo_headers(client, "college.admin@abc.campuscloud.test", "ABC001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == "VIT001"))
        course = db.scalar(select(Course).where(Course.tenant_id == tenant.id, Course.course_code == "MCA501"))
        student = db.scalar(select(Student).where(Student.tenant_id == tenant.id, Student.registration_number == "26MCA0180"))
        course_id, student_id = course.id, student.id
    roster = client.get(f"/api/v1/courses/{course_id}/students", headers=vit_admin)
    assert roster.status_code == 200 and any(item["id"] == student_id for item in roster.json())
    assert client.get(f"/api/v1/courses/{course_id}/students", headers=abc_admin).status_code == 404
    removal = client.delete(f"/api/v1/courses/{course_id}/students/{student_id}", headers=vit_admin)
    assert removal.status_code == 204
    assert all(item["id"] != student_id for item in client.get(f"/api/v1/courses/{course_id}/students", headers=vit_admin).json())
    assert client.delete(f"/api/v1/courses/{course_id}", headers=vit_admin).status_code == 409


def test_attendance_and_exam_rosters_support_authorized_entry(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    admin = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    with client.test_session_local() as db:  # type: ignore[attr-defined]
        tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == "VIT001"))
        course = db.scalar(select(Course).where(Course.tenant_id == tenant.id, Course.course_code == "MCA501"))
        exam = db.scalar(select(Examination).where(Examination.tenant_id == tenant.id, Examination.course_id == course.id, Examination.title == "Mid-Term Examination 2026"))
        student = db.scalar(select(Student).where(Student.tenant_id == tenant.id, Student.registration_number == "26MCA0180"))
        course_id, exam_id, student_id = course.id, exam.id, student.id
    day = "2026-12-02"
    roster = client.get(f"/api/v1/attendance/roster?course_id={course_id}&date={day}", headers=admin)
    assert roster.status_code == 200 and any(item["student_id"] == student_id for item in roster.json())
    saved = client.post("/api/v1/attendance/bulk", headers=admin, json={
        "course_id":course_id,"date":day,"records":[{"student_id":student_id,"status":"absent"}],
    })
    assert saved.status_code == 201 and saved.json()[0]["status"] == "absent"
    exam_roster = client.get(f"/api/v1/examinations/{exam_id}/roster", headers=admin)
    assert exam_roster.status_code == 200 and any(item["student_id"] == student_id for item in exam_roster.json())


def test_csv_preview_commit_validation_and_tenant_isolation(client_with_seeded_demo: TestClient) -> None:
    client = client_with_seeded_demo
    vit_admin = _demo_headers(client, "college.admin@vit.campuscloud.test", "VIT001")
    abc_admin = _demo_headers(client, "college.admin@abc.campuscloud.test", "ABC001")
    student_csv = "student_id,name,email,phone,department,semester,enrollment_date\nCSV-26,Jamie Import,jamie.import@example.test,+1 555 0199,MCA,1,2026-09-01\n"
    preview = client.post("/api/v1/imports/students/preview", headers=vit_admin, json={"csv_text":student_csv})
    assert preview.status_code == 200 and preview.json()["valid_count"] == 1
    committed = client.post("/api/v1/imports/students/commit", headers=vit_admin, json={"csv_text":student_csv})
    assert committed.status_code == 200 and committed.json()["created_or_updated"] == 1
    duplicate = client.post("/api/v1/imports/students/preview", headers=vit_admin, json={"csv_text":student_csv})
    assert duplicate.json()["valid_count"] == 0 and duplicate.json()["error_count"] == 1
    repeated_email_csv = "student_id,name,email,department,semester\nCSV-DUP-1,One Student,shared.import@example.test,MCA,1\nCSV-DUP-2,Two Student,shared.import@example.test,MCA,1\n"
    repeated_email = client.post("/api/v1/imports/students/preview", headers=vit_admin, json={"csv_text":repeated_email_csv})
    assert repeated_email.json()["valid_count"] == 1 and repeated_email.json()["error_count"] == 1
    cross_tenant = client.post("/api/v1/imports/attendance/preview", headers=vit_admin, json={
        "csv_text":"course_code,student_id,date,status\nCS301,ABC-CS-001,2026-12-03,present\n",
    })
    assert cross_tenant.status_code == 200 and cross_tenant.json()["valid_count"] == 0
    assert cross_tenant.json()["errors"]
    assert client.post("/api/v1/imports/students/preview", headers=abc_admin, json={"csv_text":student_csv}).json()["valid_count"] == 1
    faculty_csv = "faculty_id,name,email,phone,department,designation\nCSV-F1,Sam Instructor,sam.import@example.test,+1 555 0198,MCA,Professor\n"
    assert client.post("/api/v1/imports/faculty/preview", headers=vit_admin, json={"csv_text":faculty_csv}).json()["valid_count"] == 1
    assert client.post("/api/v1/imports/faculty/commit", headers=vit_admin, json={"csv_text":faculty_csv}).json()["created_or_updated"] == 1
    course_csv = "course_code,title,department,semester,credits,description\nVIT-IMPORT,Imported course,MCA,1,3,CSV import check\n"
    assert client.post("/api/v1/imports/courses/preview", headers=vit_admin, json={"csv_text":course_csv}).json()["valid_count"] == 1
    assert client.post("/api/v1/imports/courses/commit", headers=vit_admin, json={"csv_text":course_csv}).json()["created_or_updated"] == 1
    assert client.post("/api/v1/imports/courses/preview", headers=vit_admin, json={"csv_text":course_csv}).json()["error_count"] == 1
    faculty_headers = _demo_headers(client, "rajesh.sundaram@vit.edu", "VIT001")
    assert client.post("/api/v1/imports/courses/preview", headers=faculty_headers, json={"csv_text":course_csv}).status_code == 403
    attendance_csv = "course_code,student_id,date,status\nMCA501,26MCA0180,2026-12-03,late\n"
    attendance_preview = client.post("/api/v1/imports/attendance/preview", headers=vit_admin, json={"csv_text":attendance_csv})
    assert attendance_preview.json()["valid_count"] == 1
    assert client.post("/api/v1/imports/attendance/commit", headers=vit_admin, json={"csv_text":attendance_csv}).json()["created_or_updated"] == 1
    assert client.post("/api/v1/imports/attendance/commit", headers=vit_admin, json={"csv_text":attendance_csv}).json()["created_or_updated"] == 1
    marks_csv = "course_code,exam_title,student_id,marks_obtained\nMCA501,Mid-Term Examination 2026,26MCA0180,94\n"
    assert client.post("/api/v1/imports/marks/preview", headers=vit_admin, json={"csv_text":marks_csv}).json()["valid_count"] == 1
    assert client.post("/api/v1/imports/marks/commit", headers=vit_admin, json={"csv_text":marks_csv}).json()["created_or_updated"] == 1
