"""Tests for token crypto edge cases: TTL, wrong secret, and tampering.
Focuses on security boundaries for WOPI access tokens.
"""

from __future__ import annotations

import time
import jwt
import pytest
from hypothesis import given, strategies as st

from src.lib.crypto import decode_token, encode_token

SECRET = "test-secret-key-1234567890-abcdefgh"
OTHER_SECRET = "different-secret-key-0987654321-ihgfedcb"

def test_token_expiry_edge():
    """Verify that tokens expire exactly when they should."""
    now = time.time()
    # TTL of 1 second, issued 2 seconds ago -> expired
    token_expired = encode_token(SECRET, {"id": "1"}, ttl=1, now=now - 2)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(SECRET, token_expired)
    
    # TTL of 10 seconds, issued 2 seconds ago -> still valid
    token_valid = encode_token(SECRET, {"id": "2"}, ttl=10, now=now - 2)
    claims = decode_token(SECRET, token_valid)
    assert claims["id"] == "2"

def test_wrong_secret_rejection():
    """Verify that tokens signed with a different secret are rejected."""
    token = encode_token(SECRET, {"id": "1"}, ttl=3600)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(OTHER_SECRET, token)

def test_token_tampering():
    """Verify that modifying the token payload invalidates the signature."""
    token = encode_token(SECRET, {"user": "alice", "role": "user"}, ttl=3600)
    
    # JWT is header.payload.signature
    parts = token.split(".")
    if len(parts) != 3:
        pytest.fail("JWT did not have 3 parts")
    
    # The payload is base64 encoded. We don't need to decode it to tamper;
    # just changing a character in the payload section should break the signature.
    payload = parts[1]
    tampered_payload = payload[:-1] + ("A" if payload[-1] != "A" else "B")
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
    
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(SECRET, tampered_token)

@given(
    secret=st.text(min_size=32), 
    claims=st.dictionaries(st.text(), st.text(), min_size=1).filter(lambda d: "iat" not in d and "exp" not in d), 
    ttl=st.integers(min_value=1, max_value=86400)
)
def test_crypto_property_roundtrip(secret, claims, ttl):
    """Property test: any valid secret and claims should roundtrip correctly."""
    token = encode_token(secret, claims, ttl=ttl)
    decoded = decode_token(secret, token)
    for k, v in claims.items():
        assert decoded[k] == v
    assert decoded["exp"] - decoded["iat"] == ttl
