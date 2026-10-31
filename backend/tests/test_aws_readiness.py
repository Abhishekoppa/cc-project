from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models import Base, Membership, Tenant
from app.security import cognito
from app.security.cognito import get_current_principal
from app.services.memberships import get_active_memberships
from app.services.object_storage import create_download_url, create_upload_form


def test_production_database_url_uses_tls_and_escapes_credentials() -> None:
    config = Settings(
        database_url=None,
        app_env="production",
        database_host="db.internal",
        database_port=5432,
        database_name="campuscloud",
        database_user="api user",
        database_password="secret/with?reserved",
        database_sslmode="verify-full",
    )

    assert config.resolved_database_url.startswith("postgresql+psycopg://api%20user:")
    assert "@db.internal:5432/campuscloud?sslmode=verify-full" in config.resolved_database_url


def test_production_database_refuses_unverified_or_missing_configuration() -> None:
    with pytest.raises(ValueError, match="verify-full"):
        Settings(
            app_env="production",
            database_host="db.internal",
            database_user="app",
            database_password="password",
            database_sslmode="require",
        ).resolved_database_url
    with pytest.raises(ValueError, match="required"):
        Settings(app_env="production", database_url=None).resolved_database_url


def test_production_authentication_rejects_missing_bearer_token() -> None:
    with pytest.raises(HTTPException) as exc:
        get_current_principal(None)
    assert exc.value.status_code == 401


def test_development_identity_requires_explicit_development_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "allow_dev_auth", True)
    monkeypatch.setattr(settings, "demo_auth_password", "development-test-password")
    monkeypatch.setattr(settings, "demo_auth_secret", "local-demo-signing-secret-for-tests-with-at-least-32-bytes")
    now = datetime.now(timezone.utc)
    signed = cognito.jwt.encode(
        {
            "sub": "demo-admin",
            "iss": cognito.DEMO_TOKEN_ISSUER,
            "iat": now,
            "exp": now + timedelta(hours=1),
            "token_use": "access",
        },
        settings.demo_auth_secret,
        algorithm="HS256",
    )
    token = HTTPAuthorizationCredentials(scheme="Bearer", credentials=signed)
    principal = get_current_principal(token)
    assert principal.subject == "demo-admin"

    with pytest.raises(HTTPException) as invalid:
        get_current_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials="dev:demo-admin"))
    assert invalid.value.status_code == 401

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "aws_region", None)
    monkeypatch.setattr(settings, "cognito_user_pool_id", None)
    monkeypatch.setattr(settings, "cognito_app_client_id", None)
    with pytest.raises(HTTPException) as exc:
        get_current_principal(token)
    assert exc.value.status_code == 503


def test_staging_identity_can_be_enabled_but_production_cannot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "allow_dev_auth", True)
    monkeypatch.setattr(settings, "demo_auth_password", "staging-demo-password")
    monkeypatch.setattr(settings, "demo_auth_secret", "staging-demo-signing-secret-for-tests-with-at-least-32-bytes")
    now = datetime.now(timezone.utc)
    signed = cognito.jwt.encode(
        {
            "sub": "staging-test-user",
            "iss": cognito.DEMO_TOKEN_ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "token_use": "access",
        },
        settings.demo_auth_secret,
        algorithm="HS256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=signed)
    assert get_current_principal(credentials).subject == "staging-test-user"

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "aws_region", None)
    monkeypatch.setattr(settings, "cognito_user_pool_id", None)
    monkeypatch.setattr(settings, "cognito_app_client_id", None)
    with pytest.raises(HTTPException) as exc:
        get_current_principal(credentials)
    assert exc.value.status_code == 503


def test_cognito_access_token_claims_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeJwkClient:
        def __init__(self, url: str) -> None:
            assert url == "https://cognito-idp.ap-south-1.amazonaws.com/pool-123/.well-known/jwks.json"

        def get_signing_key_from_jwt(self, token: str):
            return type("Key", (), {"key": "verified-rsa-public-key"})()

    monkeypatch.setattr(settings, "aws_region", "ap-south-1")
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool-123")
    monkeypatch.setattr(settings, "cognito_app_client_id", "client-123")
    monkeypatch.setattr(settings, "cognito_audience", None)
    monkeypatch.setattr(cognito, "PyJWKClient", FakeJwkClient)
    monkeypatch.setattr(
        cognito.jwt,
        "decode",
        lambda *args, **kwargs: {"sub": "user-123", "token_use": "access", "client_id": "client-123"},
    )

    claims = cognito.decode_cognito_access_token("signed.jwt.token")
    assert claims["sub"] == "user-123"

    monkeypatch.setattr(
        cognito.jwt,
        "decode",
        lambda *args, **kwargs: {"sub": "user-123", "token_use": "id", "client_id": "client-123"},
    )
    with pytest.raises(HTTPException) as exc:
        cognito.decode_cognito_access_token("signed.jwt.token")
    assert exc.value.status_code == 401


def test_membership_lookup_returns_only_active_member_and_tenant() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db, db.begin():
        active = Tenant(tenant_code="A001", name="Active College", slug="active", status="active")
        suspended_tenant = Tenant(tenant_code="B001", name="Suspended College", slug="suspended", status="suspended")
        db.add_all([active, suspended_tenant])
        db.flush()
        db.add_all([
            Membership(tenant_id=active.id, subject="user-123", role="college_admin", status="active"),
            Membership(tenant_id=suspended_tenant.id, subject="user-123", role="student", status="active"),
            Membership(tenant_id=active.id, subject="user-456", role="college_admin", status="suspended"),
        ])

    with Session(engine) as db:
        results = get_active_memberships(db, "user-123")
        suspended_results = get_active_memberships(db, "user-456")

    assert [membership.tenant.tenant_code for membership in results] == ["A001"]
    assert suspended_results == []


def test_s3_upload_is_private_tenant_scoped_limited_and_expiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "aws_region", "ap-south-1")
    monkeypatch.setattr(settings, "s3_bucket", "private-bucket")
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    class FakeS3:
        kwargs = None

        def generate_presigned_post(self, **kwargs):
            self.kwargs = kwargs
            return {"url": "https://private-bucket.s3.amazonaws.com", "fields": {"key": kwargs["Key"]}}

    s3 = FakeS3()

    result = create_upload_form("tenant-123", "application/pdf", s3_client=s3)
    assert result["fields"]["key"].startswith("tenants/tenant-123/")
    assert ["content-length-range", 1, 1024] in s3.kwargs["Conditions"]
    assert s3.kwargs["ExpiresIn"] == 300

    with pytest.raises(HTTPException) as exc:
        create_upload_form("tenant-123", "application/x-msdownload", s3_client=s3)
    assert exc.value.status_code == 415


def test_s3_download_rejects_another_tenant_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "s3_bucket", "private-bucket")
    with pytest.raises(HTTPException) as exc:
        create_download_url("tenant-a", "tenants/tenant-b/private.pdf")
    assert exc.value.status_code == 404
