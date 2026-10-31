# CampusCloud Backend

## Local demo authentication in an isolated staging deployment

Local demo authentication is allowed only when all of these backend settings are present:

```text
APP_ENV=staging
ALLOW_DEV_AUTH=true
DEMO_AUTH_PASSWORD=<unique strong staging-only password>
DEMO_AUTH_SECRET=<random staging-only signing secret, at least 32 bytes>
```

Use a dedicated staging service and a separate test database or isolated RDS database. Do not point staging at production records. Store both demo values in AWS Secrets Manager; do not put them in an image, source file, or public GitHub Actions variable. Restrict access to the staging URL (for example, with an IP allowlist or VPN).

Build the staging frontend with `VITE_DEMO_AUTH_ENABLED=true` (for Docker, pass `--build-arg VITE_DEMO_AUTH_ENABLED=true`). Keep production frontend builds at `false`.

After configuring the staging environment, run the existing Alembic migrations and `python -m scripts.seed_demo` against the staging database. Seeded demo accounts use `DEMO_AUTH_PASSWORD`; registrations and role assignments remain stored in PostgreSQL.

The production configuration remains `APP_ENV=production` and `ALLOW_DEV_AUTH=false`. The backend rejects local demo tokens in production even if the demo flags are accidentally set; production sign-in uses Cognito.
