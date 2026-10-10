"""Authentication and supervisor identity helper for HeatShield.

Supports Clerk session JWTs, supervisor API keys, and offline demo identities.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("heatshield.auth")

DEFAULT_SUPERVISOR = {
    "user_id": "demo_supervisor_01",
    "name": "John Doe",
    "email": "john.doe@heatshield.gov.in",
    "role": "Field Safety Supervisor",
    "organization": "Apex Infrastructure & Field Safety",
    "phone": "+91 98765 43210",
    "certifications": [
        "OSHA 30-Hour Construction Safety",
        "National Heat Action Plan Certified",
        "First Aid & Heat Stroke Triage",
    ],
    "assigned_sites": ["demo", "hyd-metro-p4"],
    "provider": "demo",
    "authenticated": True,
}


def decode_jwt_unverified(token: str) -> dict[str, Any]:
    """Decode JWT payload without verifying cryptographic signature.

    Safely extracts subject, email, expiration, and custom metadata claims
    from Clerk session tokens in serverless/offline environments.
    """
    if not token or not isinstance(token, str):
        return {}

    parts = token.strip().split(".")
    if len(parts) != 3:
        return {}

    payload_b64 = parts[1]
    # Add standard base64 padding if required
    rem = len(payload_b64) % 4
    if rem > 0:
        payload_b64 += "=" * (4 - rem)

    try:
        decoded_bytes = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        data = json.loads(decoded_bytes.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to decode JWT payload: %s", exc)
        return {}


def get_token_from_event(event: dict[str, Any]) -> str | None:
    """Extract Bearer token or API key from request event headers."""
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    auth_header = headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return headers.get("x-api-key") or headers.get("x-clerk-auth-token")


def get_caller_identity(event: dict[str, Any]) -> dict[str, Any]:
    """Determine the supervisor's identity from event authorization."""
    token = get_token_from_event(event)

    if token:
        # Check if token is a JWT (e.g. from Clerk)
        claims = decode_jwt_unverified(token)
        if claims:
            now = time.time()
            exp = claims.get("exp")
            is_expired = bool(exp and isinstance(exp, (int, float)) and exp < now)

            sub = str(claims.get("sub", ""))
            email = (
                claims.get("email")
                or claims.get("email_address")
                or claims.get("primary_email_address")
                or f"{sub}@clerk.user"
            )
            name = (
                claims.get("name")
                or claims.get("full_name")
                or f"{claims.get('first_name', '')} {claims.get('last_name', '')}".strip()
                or "Supervisor"
            )

            org = claims.get("org_slug") or claims.get("org_id") or "HeatShield Safety Operations"
            role = claims.get("role") or claims.get("org_role") or "Safety Supervisor"

            return {
                "user_id": sub,
                "name": name,
                "email": email,
                "role": role,
                "organization": org,
                "certifications": [
                    "OSHA 30-Hour Construction Safety",
                    "Clerk Authenticated Supervisor",
                    "Heat Illness Prevention & First Aid",
                ],
                "assigned_sites": ["demo"],
                "provider": "clerk",
                "authenticated": not is_expired,
                "expired": is_expired,
                "claims": claims,
            }

        # Check if token matches SUPERVISOR_KEY
        expected_key = os.getenv("SUPERVISOR_KEY")
        if expected_key and token == expected_key:
            return {
                **DEFAULT_SUPERVISOR,
                "user_id": "key_supervisor",
                "name": "Authorized Supervisor",
                "provider": "key",
                "authenticated": True,
            }

    # Default fallback demo supervisor
    return dict(DEFAULT_SUPERVISOR)


def is_authorized(event: dict[str, Any]) -> bool:
    """Validate whether the incoming request is authorized for supervisor actions."""
    expected_key = os.getenv("SUPERVISOR_KEY")
    clerk_secret = os.getenv("CLERK_SECRET_KEY")

    # If no strict supervisor key or clerk secret is configured in the environment,
    # allow requests (dev / demo / offline evaluation mode).
    if not expected_key and not clerk_secret:
        return True

    token = get_token_from_event(event)
    if not token:
        return False

    # Check supervisor API key
    if expected_key and token == expected_key:
        return True

    # Check Clerk JWT
    claims = decode_jwt_unverified(token)
    if claims:
        # Verify expiration
        exp = claims.get("exp")
        now = time.time()
        if exp and isinstance(exp, (int, float)) and exp < now:
            return False

        # If CLERK_SECRET_KEY is configured, confirm claims indicate an active sub
        if claims.get("sub"):
            return True

    return False
