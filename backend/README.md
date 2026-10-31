# CampusCloud Backend

## First-party password authentication

The API uses CampusCloud-managed accounts when `AUTH_PROVIDER=local` (the default). Public registration creates Student accounts only. Passwords are stored as salted scrypt hashes in PostgreSQL. The API issues short-lived signed access tokens; tenant and role permissions continue to come from active database memberships.

Configure the backend with:

```text
APP_ENV=production
AUTH_PROVIDER=local
AUTH_JWT_SECRET=<random secret with at least 32 bytes>
AUTH_TOKEN_TTL_MINUTES=30
DATABASE_SSLMODE=verify-full
```

Store `AUTH_JWT_SECRET` in AWS Secrets Manager and inject it into the ECS task as a secret. Do not use `DEMO_AUTH_SECRET` or the shared `DEMO_AUTH_PASSWORD` for production accounts. Set `ALLOW_DEV_AUTH=false`; the shared seeded demo sign-in is limited to development/staging.

### Provisioning existing roles

Public registration cannot create faculty, College Admin, or Super Admin accounts. First create the campus/student/faculty data, then provision each login through a trusted backend shell. The command prompts for the password without echoing it:

```powershell
python -m scripts.provision_account --tenant-code VIT001 --email college.admin@vit.campuscloud.test --name "VIT College Admin" --role college_admin
python -m scripts.provision_account --tenant-code VIT001 --email rajesh.sundaram@vit.edu --name "Dr. Rajesh Sundaram" --role faculty
python -m scripts.provision_account --tenant-code VIT001 --email abhishek.poudel@vit.edu --name "Abhishek Poudel" --role student
```

The script creates or resets a tenant-scoped local account and active membership. Student and Faculty logins require a matching existing profile. Existing Cognito passwords cannot be migrated; provision local passwords for those accounts before switching traffic.

### Important deployment limits

Local registration currently does not verify email ownership. Login throttling, MFA, password-reset email, and session revocation are not implemented. This implementation is suitable for a controlled college demonstration after account provisioning; complete those controls before using it for public production accounts or sensitive records. OWASP recommends adaptive password hashing and login throttling; see its [password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) and [authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) guidance.
