"""Create or reset a local login for an existing tenant account."""

from __future__ import annotations

import argparse
import getpass
import re

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Faculty, LocalAccount, Membership, Student, Tenant
from app.security.passwords import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-code", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--role",
        choices=("college_admin", "faculty", "student", "platform_admin"),
        required=True,
    )
    args = parser.parse_args()
    email = args.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        parser.error("--email must be a valid email address")

    password = getpass.getpass("New password (minimum 12 characters): ")
    if len(password) < 12:
        parser.error("Password must be at least 12 characters")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match")

    with SessionLocal.begin() as db:
        tenant = db.scalar(
            select(Tenant).where(
                Tenant.tenant_code == args.tenant_code.strip().upper(),
                Tenant.status == "active",
            )
        )
        if tenant is None:
            parser.error("Active campus was not found")
        tenant_code = tenant.tenant_code

        account = db.scalar(
            select(LocalAccount).where(
                LocalAccount.tenant_id == tenant.id,
                func.lower(LocalAccount.email) == email,
            )
        )
        if account is None:
            account = LocalAccount(
                tenant_id=tenant.id,
                full_name=" ".join(args.name.split()),
                email=email,
                password_hash=hash_password(password),
            )
            db.add(account)
            db.flush()
        else:
            account.full_name = " ".join(args.name.split())
            account.password_hash = hash_password(password)

        subject = f"local:{account.id}"
        if args.role == "student":
            profile = db.scalar(
                select(Student).where(
                    Student.tenant_id == tenant.id,
                    func.lower(Student.email) == email,
                )
            )
            if profile is None:
                parser.error("A matching student profile must exist before provisioning a student login")
            if profile.auth_subject not in {None, subject}:
                parser.error("The student profile is already linked to another login")
            profile.auth_subject = subject
        elif args.role == "faculty":
            profile = db.scalar(
                select(Faculty).where(
                    Faculty.tenant_id == tenant.id,
                    func.lower(Faculty.email) == email,
                )
            )
            if profile is None:
                parser.error("A matching faculty profile must exist before provisioning a faculty login")
            if profile.auth_subject not in {None, subject}:
                parser.error("The faculty profile is already linked to another login")
            profile.auth_subject = subject

        membership = db.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant.id,
                Membership.subject == subject,
            )
        )
        if membership is None:
            db.add(Membership(
                tenant_id=tenant.id,
                subject=subject,
                role=args.role,
                status="active",
            ))
        else:
            membership.role = args.role
            membership.status = "active"

    print(f"Provisioned {args.role} login for {email} at {tenant_code}.")


if __name__ == "__main__":
    main()
