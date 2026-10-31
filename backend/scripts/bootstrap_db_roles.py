"""Create the least-privilege runtime role after applying schema migrations as RDS admin."""
import os

import psycopg
from psycopg import sql

from app.core.config import settings


def bootstrap() -> None:
    app_username = os.environ.get("APP_DB_USERNAME")
    app_password = os.environ.get("APP_DB_PASSWORD")
    admin_username = settings.database_user
    if not app_username or not app_password or not admin_username:
        raise RuntimeError("Admin and application DB credentials must be configured for role bootstrap")
    if app_username == admin_username:
        raise RuntimeError("The application database role must differ from the RDS administrator")

    with psycopg.connect(settings.resolved_database_url, autocommit=True) as connection:
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_username,)).fetchone()
        role_statement = sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}" if exists else "CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(app_username), sql.Literal(app_password)
        )
        connection.execute(role_statement)
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(settings.database_name), sql.Identifier(app_username)
            )
        )
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app_username)))
        connection.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                sql.Identifier(app_username)
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(app_username))
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(sql.Identifier(admin_username), sql.Identifier(app_username))
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ).format(sql.Identifier(admin_username), sql.Identifier(app_username))
        )
    print("Least-privilege application database role is ready.")


if __name__ == "__main__":
    bootstrap()
