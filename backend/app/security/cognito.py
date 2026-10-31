from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKClientError

from app.core.config import settings

bearer = HTTPBearer(auto_error=False)
LOCAL_TOKEN_ISSUER = "campuscloud-local-auth"
LOCAL_TOKEN_AUDIENCE = "campuscloud-api"
# Temporary compatibility name for existing development tests and integrations.
DEMO_TOKEN_ISSUER = LOCAL_TOKEN_ISSUER


@dataclass(frozen=True)
class CurrentPrincipal:
    subject: str
    claims: dict[str, Any]


def decode_cognito_access_token(token: str) -> dict[str, Any]:
    issuer = settings.cognito_issuer
    client_id = settings.cognito_app_client_id
    if not issuer or not client_id:
        raise HTTPException(status_code=503, detail="Cognito authentication is not configured")

    jwks = PyJWKClient(f"{issuer}/.well-known/jwks.json")
    try:
        signing_key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=settings.cognito_audience,
            options={
                "verify_aud": settings.cognito_audience is not None,
                "require": ["exp", "iss", "sub", "token_use", "client_id"],
            },
        )
    except PyJWKClientConnectionError as exc:
        raise HTTPException(status_code=503, detail="Cognito signing keys are temporarily unavailable") from exc
    except (PyJWKClientError, jwt.PyJWTError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc

    if claims.get("token_use") != "access" or claims.get("client_id") != client_id:
        raise HTTPException(status_code=401, detail="Access token is not valid for this application")
    return claims


def decode_cognito_id_token(token: str) -> dict[str, Any]:
    """Validate Cognito's ID token when completing an email-verified registration."""
    issuer = settings.cognito_issuer
    client_id = settings.cognito_app_client_id
    if not issuer or not client_id:
        raise HTTPException(status_code=503, detail="Cognito authentication is not configured")

    jwks = PyJWKClient(f"{issuer}/.well-known/jwks.json")
    try:
        signing_key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=client_id,
            options={"require": ["exp", "iss", "sub", "token_use", "email", "email_verified"]},
        )
    except PyJWKClientConnectionError as exc:
        raise HTTPException(status_code=503, detail="Cognito signing keys are temporarily unavailable") from exc
    except (PyJWKClientError, jwt.PyJWTError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired Cognito identity token") from exc

    if claims.get("token_use") != "id":
        raise HTTPException(status_code=401, detail="Cognito identity token is not valid for this operation")
    return claims


def create_local_access_token(subject: str) -> str:
    """Create a short-lived first-party access token for a local account."""
    secret = settings.auth_signing_secret
    if not settings.local_auth_enabled or not secret:
        raise HTTPException(status_code=503, detail="Local authentication is not configured")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject,
            "iss": LOCAL_TOKEN_ISSUER,
            "aud": LOCAL_TOKEN_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=settings.auth_token_ttl_minutes),
            "token_use": "access",
        },
        secret,
        algorithm="HS256",
    )


def decode_local_access_token(token: str) -> dict[str, Any]:
    secret = settings.auth_signing_secret
    if not settings.local_auth_enabled or not secret:
        raise HTTPException(status_code=503, detail="Local authentication is not configured")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=LOCAL_TOKEN_ISSUER,
            options={"verify_aud": False, "require": ["exp", "iat", "iss", "sub", "token_use"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc
    if claims.get("aud") not in {None, LOCAL_TOKEN_AUDIENCE}:
        raise HTTPException(status_code=401, detail="Access token is not valid for this application")
    return claims


# Backwards-compatible aliases for older development integrations.
create_demo_access_token = create_local_access_token
decode_demo_access_token = decode_local_access_token


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")

    token = credentials.credentials
    if settings.selected_auth_provider == "local":
        if not settings.local_auth_enabled:
            raise HTTPException(status_code=503, detail="Local authentication is not configured")
        claims = decode_local_access_token(token)
        if claims.get("token_use") != "access":
            raise HTTPException(status_code=401, detail="Invalid access token")
        return CurrentPrincipal(subject=claims["sub"], claims=claims)

    claims = decode_cognito_access_token(token)
    return CurrentPrincipal(subject=claims["sub"], claims=claims)
