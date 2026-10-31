import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.students import get_student, list_students
from app.api.reports import get_multi_tenant_platform_summary
from fastapi import HTTPException
from app.models import Base, Membership, Student, Tenant
from app.security.cognito import CurrentPrincipal
from app.security.tenant_context import TenantContext


@pytest.fixture
def test_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        # Create 2 distinct tenants (Slide 6 & 13)
        vit = Tenant(tenant_code="VIT001", name="Vellore Institute of Technology", slug="vit", status="active")
        abc = Tenant(tenant_code="ABC001", name="ABC College", slug="abc", status="active")
        session.add_all([vit, abc])
        session.flush()

        # Seed student in VIT
        std_vit = Student(
            tenant_id=vit.id,
            registration_number="26MCA0180",
            first_name="Abhishek",
            last_name="Poudel",
            email="abhishek@vit.edu",
            status="active",
        )
        # Seed student in ABC
        std_abc = Student(
            tenant_id=abc.id,
            registration_number="ABC-001",
            first_name="Rohan",
            last_name="Sharma",
            email="rohan@abc.edu",
            status="active",
        )
        session.add_all([std_vit, std_abc])
        session.commit()

        yield session, vit, abc


def test_tenant_isolation_student_queries(test_session):
    """Verify Tenant A (VIT) can ONLY see Tenant A data, and never Tenant B data."""
    session, vit, abc = test_session

    principal_vit = CurrentPrincipal(subject="vit-admin", claims={"sub": "vit-admin"})
    ctx_vit = TenantContext(tenant=vit, role="college_admin", principal=principal_vit)

    principal_abc = CurrentPrincipal(subject="abc-admin", claims={"sub": "abc-admin"})
    ctx_abc = TenantContext(tenant=abc, role="college_admin", principal=principal_abc)

    # 1. VIT Admin queries students -> returns ONLY VIT students
    vit_students = list_students(department=None, status_filter=None, ctx=ctx_vit, db=session)
    assert len(vit_students) == 1
    assert vit_students[0].registration_number == "26MCA0180"
    assert vit_students[0].first_name == "Abhishek"
    assert vit_students[0].tenant_id == vit.id

    # 2. ABC Admin queries students -> returns ONLY ABC students
    abc_students = list_students(department=None, status_filter=None, ctx=ctx_abc, db=session)
    assert len(abc_students) == 1
    assert abc_students[0].registration_number == "ABC-001"
    assert abc_students[0].first_name == "Rohan"
    assert abc_students[0].tenant_id == abc.id

    # 3. ABC Admin cannot access VIT student by ID
    with pytest.raises(Exception):
        get_student(student_id=vit_students[0].id, ctx=ctx_abc, db=session)


def test_tenant_admin_cannot_read_platform_wide_summary(test_session):
    session, vit, _ = test_session
    principal = CurrentPrincipal(subject="vit-admin", claims={"sub": "vit-admin"})
    ctx = TenantContext(tenant=vit, role="college_admin", principal=principal)

    with pytest.raises(HTTPException) as exc:
        get_multi_tenant_platform_summary(ctx=ctx, db=session)

    assert exc.value.status_code == 403
