import os
import logging

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.dialects.postgresql import insert
from starlette.requests import Request

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

KEYCLOAK_ISSUER_URL = os.environ.get("KEYCLOAK_ISSUER_URL")
if not KEYCLOAK_ISSUER_URL:
    raise RuntimeError("KEYCLOAK_ISSUER_URL is not set — cannot validate tokens")
JWKS_URL = f"{KEYCLOAK_ISSUER_URL}/protocol/openid-connect/certs"
_jwks_keys: list[dict] | None = None

ALLOWED_GROUP = os.environ.get("BITSWAN_ALLOWED_GROUP")
if not ALLOWED_GROUP:
    raise RuntimeError("BITSWAN_ALLOWED_GROUP is not set — cannot verify group membership")

ADMIN_GROUP = os.environ.get("BITSWAN_ADMIN_GROUP", f"{ALLOWED_GROUP}/admin")


async def _get_jwks_keys() -> list[dict]:
    """Fetch and cache JWKS public keys from Keycloak."""
    global _jwks_keys
    if _jwks_keys is not None:
        return _jwks_keys

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.get(JWKS_URL)
        resp.raise_for_status()
        _jwks_keys = resp.json().get("keys", [])
    return _jwks_keys


async def validate_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Validate a Bearer JWT token against Keycloak's JWKS keys."""

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = credentials.credentials
    try:
        keys = await _get_jwks_keys()
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        key_data = next((k for k in keys if k.get("kid") == kid), None)
        if not key_data:
            global _jwks_keys
            _jwks_keys = None
            keys = await _get_jwks_keys()
            key_data = next((k for k in keys if k.get("kid") == kid), None)
            if not key_data:
                raise HTTPException(status_code=401, detail="Unknown signing key")

        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


async def record_login(email: str, session, display_name: str | None = None) -> None:
    """Pair the OIDC user with a Person row.

    1. If a Person exists with this email, refresh its display_name from
       the claim (if provided) and bump first_seen_at on first match.
    2. Else, if a placeholder (email IS NULL) exists whose display_name
       matches the claim, set its email and first_seen_at — the
       placeholder is now resolved, and every Project that points at it
       silently picks up the email association.
    3. Else, create a fresh Person with email + display_name.

    Best-effort name match. If two placeholders share the same name,
    the oldest unpaired one is paired; the rest stay placeholders for an
    admin to clean up later.
    """
    # Lazy import to avoid circular import on module load.
    from datetime import datetime, timezone

    from sqlalchemy import select, update

    from app.models import Person

    fallback_name = display_name or email
    now = datetime.now(timezone.utc)

    # 1. Existing email → bump.
    existing = (
        await session.execute(select(Person).where(Person.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        if display_name and existing.display_name != display_name:
            existing.display_name = display_name
        if existing.first_seen_at is None:
            existing.first_seen_at = now
        await session.commit()
        return

    # 2. Placeholder by display_name → promote.
    if display_name:
        placeholder = (
            await session.execute(
                select(Person)
                .where(Person.email.is_(None), Person.display_name == display_name)
                .order_by(Person.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if placeholder is not None:
            placeholder.email = email
            placeholder.first_seen_at = now
            await session.commit()
            return

    # 3. New Person.
    session.add(Person(display_name=fallback_name, email=email, first_seen_at=now))
    await session.commit()


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Router-level dependency that validates the token and stores claims on request.state."""
    claims = await validate_token(credentials)
    groups = claims.get("group_membership", [])
    if ALLOWED_GROUP not in groups:
        raise HTTPException(
            status_code=403,
            detail=f"User not in required group: {ALLOWED_GROUP}",
        )
    request.state.claims = claims
    email = claims.get("email")
    if email:
        from app.database import async_session

        display_name = claims.get("name") or claims.get("preferred_username")
        async with async_session() as db:
            await record_login(email, db, display_name=display_name)


def get_username(claims: dict) -> str:
    return claims.get("preferred_username", "anonymous")


def get_username_from_request(request: Request) -> str:
    return get_username(request.state.claims)


def get_email(claims: dict) -> str:
    return claims.get("email", "")


def get_email_from_request(request: Request) -> str:
    return get_email(request.state.claims)


def is_admin_claims(claims: dict) -> bool:
    return ADMIN_GROUP in claims.get("group_membership", [])


async def require_admin(
    request: Request,
    _: None = Depends(require_auth),
):
    if not is_admin_claims(request.state.claims):
        raise HTTPException(
            status_code=403,
            detail=f"Admin role required (group: {ADMIN_GROUP})",
        )
