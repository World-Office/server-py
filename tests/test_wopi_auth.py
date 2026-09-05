"""Direct unit tests for the WOPI auth module — token lifecycle (UNIT).

Paradigm: **Unit tests** for ``src.wopi.auth``. Unlike the integration
suites, these tests never spin up a TestClient (which in this starlette
pin requires the optional ``httpx2``/``httpx`` package). Instead each test
builds a minimal ``fastapi.Request`` straight from an ASGI scope dict, so
the whole token lifecycle can be exercised offline and deterministically.

Covered lifecycle:

1. **Extraction** — ``token_from_request`` pulls the token from the
   ``access_token`` query parameter or the ``Authorization: Bearer``
   header, with query string taking precedence.
2. **Validation** — ``require_auth`` returns the JWT claims for a valid,
   correctly-signed, unexpired token.
3. **Rejection** — missing, garbage, wrong-secret and expired tokens all
   raise ``WopiError(401, ...)`` (fail-closed; never a 5xx).
4. **Dependency** — ``auth_dependency`` maps success to the claims dict
   and failure to a 401 ``JSONResponse`` for FastAPI.

Everything is deterministic: no network, no sleeps, no time-of-day.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

import pytest
from fastapi import Request

from src.lib.crypto import encode_token
from src.wopi.auth import auth_dependency, require_auth, token_from_request
from src.wopi.protocol import WopiError

SECRET = "0123456789abcdef0123456789abcdef"  # 32 bytes, RFC 7518 minimum
OTHER_SECRET = "fedcba9876543210fedcba9876543210"


def _request(query: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Request:
    """Build a minimal ``fastapi.Request`` from an ASGI scope.

    Deliberately *not* a TestClient: constructing a Request directly from a
    scope requires no HTTP client at all, keeping these tests hermetic.
    """
    qs = urlencode(query or {}).encode()
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": qs,
        "headers": hdrs,
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# 1. Token extraction (token_from_request)
# ---------------------------------------------------------------------------


def test_token_from_query_string():
    """The WOPI host passes ``?access_token=`` on every call; it must be read."""
    req = _request(query={"access_token": "tok-abc", "file_id": "doc1"})
    assert token_from_request(req) == "tok-abc"


def test_token_from_bearer_header():
    """``Authorization: Bearer <jwt>`` is the documented alternative transport."""
    req = _request(headers={"Authorization": "Bearer tok-bearer"})
    assert token_from_request(req) == "tok-bearer"


def test_token_prefers_query_string_over_bearer():
    """When both transports carry a token, the query string wins (WOPI
    convention — the host always sends access_token on the URL)."""
    req = _request(
        query={"access_token": "from-query"},
        headers={"Authorization": "Bearer from-header"},
    )
    assert token_from_request(req) == "from-query"


def test_token_extracts_bare_bearer_token_without_whitespace():
    """Whitespace around the Bearer token is stripped, not treated as part of it."""
    req = _request(headers={"Authorization": "Bearer   spaced-token  "})
    assert token_from_request(req) == "spaced-token"


def test_token_missing_returns_none():
    """No token anywhere (no query param, no header) yields None, never an error."""
    req = _request()
    assert token_from_request(req) is None


def test_token_ignores_non_bearer_authorization_header():
    """A Basic/Digest Authorization header carries no WOPI token and is ignored."""
    req = _request(headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert token_from_request(req) is None


def test_token_ignores_empty_query_string_value():
    """An empty access_token value counts as absent (fail-closed, not revoked)."""
    req = _request(query={"access_token": ""})
    assert token_from_request(req) is None


# ---------------------------------------------------------------------------
# 2. Validation success (require_auth)
# ---------------------------------------------------------------------------


def test_require_auth_valid_token_returns_claims():
    """A correctly-signed, unexpired token yields its full claim set."""
    token = encode_token(SECRET, {"file_id": "doc1", "user_id": "alice"}, ttl=3600)
    claims = require_auth(_request(query={"access_token": token}), SECRET)
    assert claims["file_id"] == "doc1"
    assert claims["user_id"] == "alice"
    # lifecycle bookkeeping claims are populated too
    assert "iat" in claims
    assert "exp" in claims


def test_require_auth_bearer_token_returns_claims():
    """The Bearer transport validates identically to the query-string one."""
    token = encode_token(SECRET, {"file_id": "doc2"}, ttl=3600)
    claims = require_auth(_request(headers={"Authorization": f"Bearer {token}"}), SECRET)
    assert claims["file_id"] == "doc2"


# ---------------------------------------------------------------------------
# 3. Validation failures (WopiError, fail-closed)
# ---------------------------------------------------------------------------


def test_require_auth_missing_token_raises_401():
    """A request with no token is rejected with 401, not 500."""
    with pytest.raises(WopiError) as excinfo:
        require_auth(_request(), SECRET)
    assert excinfo.value.status == 401
    assert "Missing access_token" in excinfo.value.message


def test_require_auth_garbage_token_raises_401():
    """A non-JWT string must be rejected as invalid, in one 401 path."""
    with pytest.raises(WopiError) as excinfo:
        require_auth(_request(query={"access_token": "not.a.jwt"}), SECRET)
    assert excinfo.value.status == 401
    assert "Invalid access_token" in excinfo.value.message


def test_require_auth_wrong_secret_raises_401():
    """A token signed with another secret must not pass (signature check)."""
    token = encode_token(OTHER_SECRET, {"file_id": "doc1"}, ttl=3600)
    with pytest.raises(WopiError) as excinfo:
        require_auth(_request(query={"access_token": token}), SECRET)
    assert excinfo.value.status == 401


def test_require_auth_expired_token_raises_401():
    """An expired token must be rejected — the core of the lifecycle."""
    token = encode_token(SECRET, {"file_id": "old"}, ttl=1, now=time.time() - 3600)
    with pytest.raises(WopiError) as excinfo:
        require_auth(_request(query={"access_token": token}), SECRET)
    assert excinfo.value.status == 401


def test_require_auth_altered_token_raises_401():
    """Tampering with the payload invalidates the signature and is rejected."""
    token = encode_token(SECRET, {"file_id": "doc1"}, ttl=3600)
    header, payload, sig = token.split(".")
    # flip one char in the signature so it can no longer verify
    tampered = f"{header}.{payload}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"
    with pytest.raises(WopiError) as excinfo:
        require_auth(_request(query={"access_token": tampered}), SECRET)
    assert excinfo.value.status == 401


# ---------------------------------------------------------------------------
# 4. FastAPI dependency (auth_dependency)
# ---------------------------------------------------------------------------


def test_auth_dependency_success_returns_claims():
    """On success the dependency returns the verified claims dict."""

    async def run() -> None:
        token = encode_token(SECRET, {"file_id": "doc9"}, ttl=3600)
        req = _request(query={"access_token": token})
        result = await auth_dependency(SECRET)(req)
        assert result["file_id"] == "doc9"

    import anyio

    anyio.run(run)


def test_auth_dependency_failure_returns_401_json_response():
    """On failure the dependency returns a 401 JSONResponse (not a raise)."""

    async def run() -> None:
        from fastapi.responses import JSONResponse

        result = await auth_dependency(SECRET)(_request())
        assert isinstance(result, JSONResponse)
        assert result.status_code == 401
        assert result.body  # body is a populated JSON error

    import anyio

    anyio.run(run)
