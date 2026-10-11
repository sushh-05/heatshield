"""Supervisor authentication for HeatShield.

A request is a supervisor request when it carries the SUPERVISOR_KEY or a
Clerk session token whose signature verifies. With neither, requests are
rejected unless HEATSHIELD_OPEN_ACCESS=true (local dev and demos).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from .config import open_access, secret

logger = logging.getLogger("heatshield.auth")

CLERK_JWKS_URL = "https://api.clerk.com/v1/jwks"

DEMO_SUPERVISOR = {
    "user_id": "demo_supervisor",
    "name": "Demo Supervisor",
    "email": "",
    "role": "Supervisor",
    "organization": "",
    "provider": "demo",
    "authenticated": False,
}

ANONYMOUS = {
    "user_id": "",
    "name": "",
    "email": "",
    "role": "",
    "organization": "",
    "provider": "none",
    "authenticated": False,
}

_jwks_clients: dict[str, Any] = {}


def get_token_from_event(event: dict[str, Any]) -> str | None:
    """Extract Bearer token or API key from request event headers."""
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    auth_header = headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return headers.get("x-api-key") or headers.get("x-clerk-auth-token")


def _jwks_client(secret_key: str):
    import jwt

    if secret_key not in _jwks_clients:
        _jwks_clients[secret_key] = jwt.PyJWKClient(
            CLERK_JWKS_URL,
            headers={"Authorization": f"Bearer {secret_key}"},
            cache_keys=True,
            lifespan=3600,
            timeout=5,
        )
    return _jwks_clients[secret_key]


def verify_clerk_token(token: str) -> dict[str, Any] | None:
    """Returns the claims of a Clerk session token, or None unless its signature and expiry verify.

    The verification key is CLERK_JWT_KEY (the instance's PEM public key) when
    set, otherwise the instance JWKS fetched with CLERK_SECRET_KEY.
    """
    if not token or token.count(".") != 2:
        return None
    pem = os.getenv("CLERK_JWT_KEY")
    secret_key = secret("CLERK_SECRET_KEY", "CLERK_SECRET_KEY_PARAMETER")
    if not pem and not secret_key:
        return None
    try:
        import jwt
    except ImportError:
        logger.error("PyJWT is not installed; Clerk tokens cannot be verified and are rejected")
        return None
    try:
        key = pem.replace("\\n", "\n") if pem else _jwks_client(secret_key).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            options={"require": ["exp", "sub"], "verify_aud": False},
            leeway=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Rejected Clerk token: %s", type(exc).__name__)
        return None
    allowed_parties = [p.strip() for p in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",") if p.strip()]
    if allowed_parties and claims.get("azp") not in allowed_parties:
        logger.info("Rejected Clerk token: authorized party %r is not allowed", claims.get("azp"))
        return None
    return claims


_IDENTITY_KEY = "_heatshield_identity"


def authenticate(event: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the verified supervisor identity for a request, or None. Verified once per request."""
    if _IDENTITY_KEY not in event:
        event[_IDENTITY_KEY] = _authenticate(event)
    return event[_IDENTITY_KEY]


def _authenticate(event: dict[str, Any]) -> dict[str, Any] | None:
    token = get_token_from_event(event)
    if not token:
        return None

    expected_key = secret("SUPERVISOR_KEY", "SUPERVISOR_KEY_PARAMETER")
    if expected_key and hmac.compare_digest(token.encode("utf-8"), expected_key.encode("utf-8")):
        return {
            "user_id": "key_supervisor",
            "name": "Authorized Supervisor",
            "email": "",
            "role": "Supervisor",
            "organization": "",
            "provider": "key",
            "authenticated": True,
        }

    claims = verify_clerk_token(token)
    if claims:
        sub = str(claims["sub"])
        # Clerk's current session tokens nest the organisation under "o"; older ones use org_* claims.
        org = claims.get("o") if isinstance(claims.get("o"), dict) else {}
        name = (
            claims.get("name")
            or claims.get("full_name")
            or f"{claims.get('first_name', '')} {claims.get('last_name', '')}".strip()
            or "Supervisor"
        )
        return {
            "user_id": sub,
            "name": name,
            "email": claims.get("email") or claims.get("email_address") or claims.get("primary_email_address") or "",
            "role": claims.get("role") or claims.get("org_role") or org.get("rol") or "Supervisor",
            # The organisation id is stable; a slug can be renamed, so it is only a fallback.
            "organization": str(claims.get("org_id") or org.get("id") or claims.get("org_slug") or org.get("slg") or ""),
            "provider": "clerk",
            "authenticated": True,
        }
    return None


def get_caller_identity(event: dict[str, Any]) -> dict[str, Any]:
    """The verified identity, else the demo identity in open-access mode, else anonymous."""
    identity = authenticate(event)
    if identity:
        return identity
    return dict(DEMO_SUPERVISOR if open_access() else ANONYMOUS)


def is_authorized(event: dict[str, Any]) -> bool:
    """Whether the request may perform supervisor actions."""
    return open_access() or authenticate(event) is not None
