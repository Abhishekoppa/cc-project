from __future__ import annotations

import hmac
import re
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.models import (
    Faculty,
    LocalAccount,
    Membership,
    RegistrationIntent,
    Student,
    Tenant,
)
from app.security.cognito import create_local_access_token, decode_cognito_id_token
from app.security.passwords import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])

DEMO_ADMIN_SUBJECTS = {
    "college.admin@vit.campuscloud.test": "demo:VIT001:college-admin",
    "college.admin@abc.campuscloud.test": "demo:ABC001:college-admin",
    "college.admin@xyz.campuscloud.test": "demo:XYZ001:college-admin",
    "superadmin@campuscloud.test": "demo-platform-admin",
}


class DemoLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    tenant_code: str = Field(min_length=1, max_length=32)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("tenant_code")
    @classmethod
    def normalize_tenant_code(cls, value: str) -> str:
        return value.strip().upper()


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    confirm_password: str = Field(min_length=12, max_length=256)
    tenant_code: str = Field(min_length=1, max_length=32)
    # The server always assigns student. Accept the field only to return an explicit
    # authorization error if a modified client attempts a privileged role.
    role: str | None = None

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        name = " ".join(value.split())
        if len(name) < 2:
            raise ValueError("Enter your full name")
        return name

    @field_validator("email")
    @classmethod
    def normalize_registration_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("tenant_code")
    @classmethod
    def normalize_registration_tenant(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not (
            re.search(r"[a-z]", value)
            and re.search(r"[A-Z]", value)
            and re.search(r"\d", value)
            and re.search(r"[^A-Za-z0-9]", value)
        ):
            raise ValueError("Use at least 12 characters with uppercase, lowercase, a number, and a symbol")
        return value

    @model_validator(mode="after")
    def passwords_match(self) -> "RegistrationRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class ConfirmRegistrationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    confirmation_code: str = Field(min_length=4, max_length=16)

    @field_validator("email")
    @classmethod
    def normalize_confirmation_email(cls, value: str) -> str:
        return normalize_email(value)


class CompleteRegistrationRequest(BaseModel):
    id_token: str = Field(min_length=20, max_length=8192)


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        raise ValueError("Enter a valid email address")
    return normalized


def local_demo_auth_enabled() -> bool:
    return settings.local_demo_auth_enabled


def get_active_tenant(db: Session, tenant_code: str) -> Tenant:
    tenant = db.scalar(
        select(Tenant).where(Tenant.tenant_code == tenant_code, Tenant.status == "active")
    )
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campus not found")
    return tenant


def split_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split()
    return parts[0], " ".join(parts[1:]) or parts[0]


def create_student_record(
    db: Session,
    tenant: Tenant,
    full_name: str,
    email: str,
    auth_subject: str,
) -> Student:
    existing = db.scalar(
        select(Student).where(Student.tenant_id == tenant.id, func.lower(Student.email) == email.lower())
    )
    if existing is not None:
        if existing.auth_subject not in {None, auth_subject}:
            raise HTTPException(status_code=409, detail="A student profile is already linked to another account")
        existing.auth_subject = auth_subject
        return existing
    first_name, last_name = split_full_name(full_name)
    student = Student(
        tenant_id=tenant.id,
        registration_number=f"CC-{uuid4().hex[:16].upper()}",
        first_name=first_name,
        last_name=last_name,
        email=email,
        auth_subject=auth_subject,
        status="active",
    )
    db.add(student)
    db.flush()
    return student


def cognito_client():
    if not settings.aws_region or not settings.cognito_app_client_id:
        raise HTTPException(status_code=503, detail="Cognito registration is not configured")
    # Cognito SignUp and ConfirmSignUp are public user-pool operations. No AWS
    # credentials, client secret, or elevated task-role permissions are required.
    return boto3.client("cognito-idp", region_name=settings.aws_region)


def tenant_payload(tenant: Tenant) -> dict[str, str]:
    return {"tenant_code": tenant.tenant_code, "name": tenant.name, "slug": tenant.slug}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegistrationRequest, db: Session = Depends(get_db)) -> dict:
    if payload.role is not None and payload.role.strip().lower() != "student":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Public registration is limited to student accounts")

    tenant = get_active_tenant(db, payload.tenant_code)

    if settings.selected_auth_provider == "local":
        if not settings.local_auth_enabled:
            raise HTTPException(status_code=503, detail="Local authentication is not configured")
        duplicate_account = db.scalar(
            select(LocalAccount).where(
                LocalAccount.tenant_id == tenant.id,
                LocalAccount.email == payload.email,
            )
        )
        seeded_membership = db.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.subject == payload.email,
            )
        )
        seeded_student = db.scalar(
            select(Student).where(Student.tenant_id == tenant.id, func.lower(Student.email) == payload.email)
        )
        seeded_faculty = db.scalar(
            select(Faculty).where(Faculty.tenant_id == tenant.id, Faculty.email == payload.email)
        )
        unlinked_provisioned_student = (
            settings.app_env == "production"
            and seeded_student is not None
            and seeded_student.auth_subject is None
        )
        if (
            duplicate_account
            or seeded_membership
            or seeded_faculty
            or payload.email in DEMO_ADMIN_SUBJECTS
            or unlinked_provisioned_student
            or (seeded_student is not None and (seeded_student.auth_subject is not None or seeded_student.status != "active"))
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists or requires authorized account provisioning",
            )

        account = LocalAccount(
            tenant_id=tenant.id,
            full_name=payload.full_name,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )
        db.add(account)
        db.flush()
        subject = f"local:{account.id}"
        db.add(Membership(tenant_id=tenant.id, subject=subject, role="student", status="active"))
        create_student_record(db, tenant, payload.full_name, payload.email, subject)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists for this campus") from exc

        return {
            "status": "registered",
            "message": "Your student account was created successfully.",
            "access_token": create_local_access_token(subject),
            "token_type": "bearer",
            "role": "student",
            "tenant": tenant_payload(tenant),
        }

    existing_intent = db.scalar(
        select(RegistrationIntent).where(RegistrationIntent.email == payload.email)
    )
    if existing_intent is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account registration with this email already exists")

    client = cognito_client()
    try:
        cognito_result = client.sign_up(
            ClientId=settings.cognito_app_client_id,
            Username=payload.email,
            Password=payload.password,
            UserAttributes=[
                {"Name": "email", "Value": payload.email},
                {"Name": "name", "Value": payload.full_name},
            ],
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "UsernameExistsException":
            raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
        if error_code in {"InvalidPasswordException", "InvalidParameterException"}:
            raise HTTPException(status_code=422, detail="The registration details do not meet Cognito requirements") from exc
        raise HTTPException(status_code=503, detail="Cognito could not complete registration") from exc

    intent = RegistrationIntent(
        tenant_id=tenant.id,
        cognito_subject=cognito_result["UserSub"],
        full_name=payload.full_name,
        email=payload.email,
        status="confirmed" if cognito_result.get("UserConfirmed") else "pending_confirmation",
    )
    db.add(intent)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account registration with this email already exists") from exc

    delivery = cognito_result.get("CodeDeliveryDetails") or {}
    confirmed = intent.status == "confirmed"
    return {
        "status": "registered" if confirmed else "confirmation_required",
        "message": "Your account was created. Sign in to continue." if confirmed else "Check your email for the Cognito confirmation code.",
        "email": payload.email,
        "confirmation_required": not confirmed,
        "delivery_medium": delivery.get("DeliveryMedium"),
        "tenant": tenant_payload(tenant),
    }


@router.post("/confirm-registration")
def confirm_registration(payload: ConfirmRegistrationRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    if settings.selected_auth_provider == "local":
        raise HTTPException(status_code=404, detail="Email confirmation is not configured for local accounts")
    intent = db.scalar(select(RegistrationIntent).where(RegistrationIntent.email == payload.email))
    if intent is None:
        raise HTTPException(status_code=404, detail="Registration was not found")
    if intent.status == "completed" or intent.status == "confirmed":
        return {"status": "confirmed", "message": "Your email is confirmed. You can sign in."}

    client = cognito_client()
    try:
        client.confirm_sign_up(
            ClientId=settings.cognito_app_client_id,
            Username=payload.email,
            ConfirmationCode=payload.confirmation_code,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"CodeMismatchException", "ExpiredCodeException", "UserNotFoundException"}:
            raise HTTPException(status_code=400, detail="The confirmation code is invalid or expired") from exc
        raise HTTPException(status_code=503, detail="Cognito could not confirm this registration") from exc

    intent.status = "confirmed"
    db.commit()
    return {"status": "confirmed", "message": "Your email is confirmed. You can sign in."}


@router.post("/complete-registration")
def complete_cognito_registration(
    payload: CompleteRegistrationRequest,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if settings.selected_auth_provider == "local":
        raise HTTPException(status_code=404, detail="External identity registration is disabled")
    claims = decode_cognito_id_token(payload.id_token)
    email = normalize_email(str(claims.get("email", "")))
    if claims.get("email_verified") is not True:
        raise HTTPException(status_code=403, detail="A verified email address is required to complete registration")

    intent = db.scalar(
        select(RegistrationIntent).where(RegistrationIntent.cognito_subject == claims["sub"])
    )
    if intent is None:
        return {"completed": False}
    if intent.email != email or intent.status not in {"confirmed", "completed"}:
        raise HTTPException(status_code=403, detail="The confirmed registration does not match this account")

    tenant = db.scalar(
        select(Tenant).where(Tenant.id == intent.tenant_id, Tenant.status == "active")
    )
    if tenant is None:
        raise HTTPException(status_code=403, detail="The selected campus is not active")

    existing_memberships = db.scalars(
        select(Membership).where(Membership.subject == claims["sub"], Membership.status == "active")
    ).all()
    if existing_memberships:
        if len(existing_memberships) != 1 or existing_memberships[0].tenant_id != tenant.id or existing_memberships[0].role != "student":
            raise HTTPException(status_code=409, detail="This account is already associated with another campus")
    else:
        db.add(Membership(tenant_id=tenant.id, subject=claims["sub"], role="student", status="active"))

    create_student_record(db, tenant, intent.full_name, email, claims["sub"])
    intent.status = "completed"
    db.commit()
    return {"completed": True}


@router.post("/login")
def login(payload: DemoLoginRequest, db: Session = Depends(get_db)) -> dict:
    """Authenticate a tenant-scoped CampusCloud account with its own password."""
    if settings.selected_auth_provider != "local" or not settings.local_auth_enabled:
        raise HTTPException(status_code=503, detail="Local authentication is not configured")

    tenant = get_active_tenant(db, payload.tenant_code)
    account = db.scalar(
        select(LocalAccount).where(
            LocalAccount.tenant_id == tenant.id,
            LocalAccount.email == payload.email,
        )
    )
    if account is not None and verify_password(payload.password, account.password_hash):
        subject = f"local:{account.id}"
        membership = db.scalar(
            select(Membership).where(
                Membership.subject == subject,
                Membership.tenant_id == tenant.id,
                Membership.status == "active",
            )
        )
    elif account is None and local_demo_auth_enabled() and hmac.compare_digest(payload.password, settings.demo_auth_password or ""):
        subject = DEMO_ADMIN_SUBJECTS.get(payload.email, payload.email)
        membership = db.scalar(
            select(Membership).where(
                Membership.subject == subject,
                Membership.tenant_id == tenant.id,
                Membership.status == "active",
            )
        )
        if membership is None:
            membership = db.scalar(
                select(Membership)
                .join(Tenant, Membership.tenant_id == Tenant.id)
                .where(
                    Membership.subject == subject,
                    Membership.role == "platform_admin",
                    Membership.status == "active",
                    Tenant.status == "active",
                )
            )
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")

    if membership is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")

    return {
        "access_token": create_local_access_token(subject),
        "token_type": "bearer",
        "role": membership.role,
        "tenant": tenant_payload(tenant),
    }


@router.post("/demo-login", include_in_schema=False)
def demo_login(payload: DemoLoginRequest, db: Session = Depends(get_db)) -> dict:
    """Compatibility sign-in for seeded development identities only."""
    if not local_demo_auth_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local demo sign-in is not enabled")

    tenant = get_active_tenant(db, payload.tenant_code)
    local_account = db.scalar(
        select(LocalAccount).where(
            LocalAccount.tenant_id == tenant.id,
            LocalAccount.email == payload.email,
        )
    )
    if local_account is not None:
        if not verify_password(payload.password, local_account.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")
        subject = f"local:{local_account.id}"
        membership = db.scalar(
            select(Membership).where(
                Membership.subject == subject,
                Membership.tenant_id == tenant.id,
                Membership.status == "active",
            )
        )
        if membership is None:
            raise HTTPException(status_code=403, detail="This account has no active campus membership")
        role = membership.role
    else:
        if not hmac.compare_digest(payload.password, settings.demo_auth_password or ""):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")
        subject = DEMO_ADMIN_SUBJECTS.get(payload.email, payload.email)
        membership = db.scalar(
            select(Membership).where(
                Membership.subject == subject,
                Membership.tenant_id == tenant.id,
                Membership.status == "active",
            )
        )
        if membership is not None:
            role = membership.role
        else:
            platform_membership = db.scalar(
                select(Membership)
                .join(Tenant, Membership.tenant_id == Tenant.id)
                .where(
                    Membership.subject == subject,
                    Membership.role == "platform_admin",
                    Membership.status == "active",
                    Tenant.status == "active",
                )
            )
            if platform_membership is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")
            role = "platform_admin"

    return {
        "access_token": create_local_access_token(subject),
        "token_type": "bearer",
        "role": role,
        "tenant": tenant_payload(tenant),
    }
