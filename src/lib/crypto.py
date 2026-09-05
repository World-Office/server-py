"""JWT helpers: encode/decode WOPI access tokens.

Uses HMAC-SHA256 with the shared secret configured in `[security]`.
WOPI/OCIS expects standard `HS256` signed JWTs. We prefer PyJWT for its
HSA verification robustness; python-jose remains a build-level fallback.
"""

from __future__ import annotations

import time
from typing import Any

import jwt


def encode_token(
    secret: str, claims: dict[str, Any], ttl: int = 3600, now: float | None = None
) -> str:
    """Create a signed JWT with an issued-at and expiry timestamp."""
    issued = int(now if now is not None else time.time())
    payload = {
        "iat": issued,
        "exp": issued + ttl,
        **claims,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict[str, Any]:
    """Decode and verify a JWT; raises jwt.InvalidTokenError on failure."""
    return jwt.decode(token, secret, algorithms=["HS256"])
