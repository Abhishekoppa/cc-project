from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_version: str = "1.0.0"
    database_url: str | None = None
    database_host: str | None = None
    database_port: int = 5432
    database_name: str = "campuscloud"
    database_user: str | None = None
    database_password: str | None = None
    database_sslmode: str | None = None
    cors_origins: str = "http://localhost:5173"
    allow_dev_auth: bool = False
    demo_auth_password: str | None = None
    demo_auth_secret: str | None = None
    aws_region: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None
    cognito_audience: str | None = None
    s3_bucket: str | None = None
    max_upload_bytes: int = 10 * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            if self.app_env == "production" and make_url(self.database_url).query.get("sslmode") != "verify-full":
                raise ValueError("Production DATABASE_URL must set sslmode=verify-full")
            return self.database_url
        if self.database_host and self.database_user and self.database_password:
            if self.app_env == "production" and self.database_sslmode != "verify-full":
                raise ValueError("Production database connections must use sslmode=verify-full")
            query = {"sslmode": self.database_sslmode} if self.database_sslmode else {}
            rendered_query = "&".join(f"{quote(key, safe='')}={quote(value, safe='')}" for key, value in query.items())
            suffix = f"?{rendered_query}" if rendered_query else ""
            return (
                "postgresql+psycopg://"
                f"{quote(self.database_user, safe='')}:{quote(self.database_password, safe='')}"
                f"@{self.database_host}:{self.database_port}/{quote(self.database_name, safe='')}"
                f"{suffix}"
            )
        if self.app_env == "production":
            raise ValueError("Production database connection settings are required")
        return "postgresql+psycopg://campuscloud:change-me-local-only@localhost:5432/campuscloud"

    @property
    def cognito_issuer(self) -> str | None:
        if self.aws_region and self.cognito_user_pool_id:
            return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"
        return None


settings = Settings()
