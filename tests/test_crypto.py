"""Tests for JWT token helpers."""

from __future__ import annotations

import jwt
import pytest

from src.lib.crypto import decode_token, encode_token

SECRET = "0123456789abcdef0123456789abcdef"  # 32 bytes, RFC 7518 minimum
OTHER_SECRET = "fedcba9876543210fedcba9876543210"


def test_roundtrip():
    token = encode_token(SECRET, {"file_id": "doc1", "user_id": "alice"}, ttl=60)
    claims = decode_token(SECRET, token)
    assert claims["file_id"] == "doc1"
    assert claims["user_id"] == "alice"


def test_has_iat_and_exp():
    token = encode_token(SECRET, {"file_id": "x"}, ttl=120)
    claims = decode_token(SECRET, token)
    assert claims["exp"] - claims["iat"] == 120


def test_wrong_secret_rejected():
    token = encode_token(SECRET, {"file_id": "x"}, ttl=60)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(OTHER_SECRET, token)


def test_expired_token_rejected():
    import time

    token = encode_token(SECRET, {"file_id": "x"}, ttl=1, now=time.time() - 100)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(SECRET, token)
