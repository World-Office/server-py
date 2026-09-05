"""WOPI authentication.

Two mechanisms are supported:

1. **Access-token mode** — the WOPI host passes `?access_token=<jwt>` on
   every call. We validate the JWT with our shared secret (`[security]
   jwt_secret`). This mirrors how OCIS signs WopiContext tokens.
2. **Bearer mode** — `Authorization: Bearer <jwt>` as an alternative.

For host-mode calls (CheckFileInfo/GetFile/PutFile) we accept the same
token; in client mode we forward the token OCIS gives us to the remote
WOPI host instead (see `src/editor/session.py`).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..lib.crypto import decode_token
from .protocol import WopiError


def token_from_request(request: Request) -> str | None:
    """Extract an access token from query string or Authorization header."""
    token = request.query_params.get("access_token")
    if token:
        return token

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def require_auth(request: Request, secret: str) -> dict:
    """Validate the caller's WOPI token; returns its claims.

    Raises WopiError(401, ...) when the token is missing or invalid.
    """
    token = token_from_request(request)
    if not token:
        raise WopiError(401, "Missing access_token")
    try:
        return decode_token(secret, token)
    except Exception as exc:  # PyJWT raises a family of errors
        raise WopiError(401, f"Invalid access_token: {exc}") from exc


def auth_dependency(secret: str):
    """FastAPI dependency factory that authenticates a request."""

    async def _check(request: Request):
        try:
            return require_auth(request, secret)
        except WopiError as err:
            return JSONResponse(status_code=err.status, content={"error": err.message})

    return _check
