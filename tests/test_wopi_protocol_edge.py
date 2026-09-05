"""WOPI protocol edge cases: malformed headers, unicode names, CheckInOut (UNIT).

Paradigm: **Unit tests** + **property-based fuzzing** for protocol boundary
defences. Focuses on the low-level primitives exported by ``wopi.protocol``:

* ``invalid_doc_id`` — path-traversal detection (slashes, nulls, traversal)
* ``WopiError`` — protocol-level failures with HTTP status codes
* ``lock_mismatch_error`` — standard lock conflict responses
* ``file_info_response`` — CheckFileInfo payload construction
* ``empty_wopi_response`` — minimal WOPI JSON body
* ``LOCK_HEADER``, ``OLD_LOCK_HEADER``, ``OVERRIDE_HEADER`` — constants

Tests are grouped by defensive layer:

1. **Path-traversal gate** — ``invalid_doc_id`` rejects malformed identifiers.
2. **Lock conflict semantics** — ``lock_mismatch_error`` produces standard WOPI 409.
3. **CheckFileInfo payload** — ``file_info_response`` builds spec-compliant JSON.
4. **Header constants** — WOPI header names match the spec.
5. **Empty response** — minimal WOPI responses are valid JSON.

Everything is deterministic: no network, no time-of-day dependence, no random.
"""

from __future__ import annotations

import pytest

from src.wopi.protocol import (
    LOCK_HEADER,
    OLD_LOCK_HEADER,
    OVERRIDE_HEADER,
    WopiError,
    empty_wopi_response,
    file_info_response,
    invalid_doc_id,
    lock_mismatch_error,
)


# ---------------------------------------------------------------------------
# 1. Path-traversal gate (invalid_doc_id)
# ---------------------------------------------------------------------------


def test_invalid_doc_id_rejects_slash():
    """Path-traversal detection: forward slashes are always rejected."""
    assert invalid_doc_id("doc/1") is True
    assert invalid_doc_id("../../../etc/passwd") is True


def test_invalid_doc_id_rejects_backslash():
    """Path-traversal detection: backslashes (Windows) are always rejected."""
    assert invalid_doc_id("doc\\1") is True
    assert invalid_doc_id("..\\..\\secret") is True


def test_invalid_doc_id_rejects_null_byte():
    """Path-traversal detection: null bytes are always rejected."""
    assert invalid_doc_id("doc\x001") is True
    assert invalid_doc_id("test\x00") is True


def test_invalid_doc_id_rejects_dot_and_dotdot():
    """Path-traversal detection: bare ``.`` and ``..`` are rejected."""
    assert invalid_doc_id(".") is True
    assert invalid_doc_id("..") is True
    # Even after decoding, ".." substring is rejected (opaque ids never contain it).
    assert invalid_doc_id("doc..id") is True


def test_invalid_doc_id_rejects_empty():
    """Path-traversal detection: empty identifiers are rejected."""
    assert invalid_doc_id("") is True


def test_invalid_doc_id_rejects_too_long():
    """Path-traversal detection: identifiers >128 chars are rejected."""
    # 129 chars: exceeds the 128-char limit in invalid_doc_id
    assert invalid_doc_id("a" * 129) is True
    # 128 chars: at the limit (allowed in invalid_doc_id, rejected elsewhere)
    assert invalid_doc_id("a" * 128) is False


def test_invalid_doc_id_accepts_valid_opaque_id():
    """Path-traversal detection: opaque identifiers (no separators) are accepted."""
    assert invalid_doc_id("doc1") is False
    assert invalid_doc_id("d1") is False
    assert invalid_doc_id("file-12345") is False
    assert invalid_doc_id("abc123def456") is False


# ---------------------------------------------------------------------------
# 2. Lock conflict semantics (lock_mismatch_error)
# ---------------------------------------------------------------------------


def test_lock_mismatch_error_status():
    """Lock mismatch produces HTTP 409 (CONFLICT) per WOPI spec."""
    err = lock_mismatch_error("expected-token", "actual-token")
    assert err.status == 409


def test_lock_mismatch_error_message():
    """Lock mismatch error message includes both tokens for debugging."""
    err = lock_mismatch_error("L1", "L2")
    assert "L1" in err.message
    assert "L2" in err.message


def test_lock_mismatch_error_is_wopi_error():
    """Lock mismatch error is a WopiError for status extraction."""
    err = lock_mismatch_error("L1", "L2")
    assert isinstance(err, WopiError)


# ---------------------------------------------------------------------------
# 3. WOPI error (WopiError)
# ---------------------------------------------------------------------------


def test_wopi_error_stores_status_and_message():
    """WopiError captures both HTTP status and human-readable message."""
    err = WopiError(404, "File not found")
    assert err.status == 404
    assert err.message == "File not found"


def test_wopi_error_inherits_exception():
    """WopiError is an Exception subclass for catch/finally semantics."""
    err = WopiError(400, "Bad request")
    assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# 4. CheckFileInfo payload (file_info_response)
# ---------------------------------------------------------------------------


def test_file_info_response_has_required_fields():
    """CheckFileInfo JSON contains all required fields per WOPI spec."""
    resp = file_info_response(
        name="doc.docx",
        version="1.0",
        size=1024,
        owner="owner1",
        user_id="user1",
        user_name="User One",
        last_modified=1234567890,
    )
    assert resp["BaseFileName"] == "doc.docx"
    assert resp["OwnerId"] == "owner1"
    assert resp["Size"] == 1024
    assert resp["UserId"] == "user1"
    assert resp["UserFriendlyName"] == "User One"
    assert resp["Version"] == "1.0"
    assert resp["LastModifiedTime"] == 1234567890


def test_file_info_response_has_default_read_only():
    """CheckFileInfo defaults ReadOnly to False (editable)."""
    resp = file_info_response(
        name="doc.docx",
        version="1.0",
        size=1024,
        owner="owner1",
        user_id="user1",
        user_name="User One",
        last_modified=1234567890,
    )
    assert resp["ReadOnly"] is False


def test_file_info_response_can_set_read_only():
    """CheckFileInfo allows overriding ReadOnly to True (view-only)."""
    resp = file_info_response(
        name="doc.docx",
        version="1.0",
        size=1024,
        owner="owner1",
        user_id="user1",
        user_name="User One",
        last_modified=1234567890,
        read_only=True,
    )
    assert resp["ReadOnly"] is True


def test_file_info_response_has_supports_fields():
    """CheckFileInfo includes feature flags for editor capabilities."""
    resp = file_info_response(
        name="doc.docx",
        version="1.0",
        size=1024,
        owner="owner1",
        user_id="user1",
        user_name="User One",
        last_modified=1234567890,
    )
    assert resp["SupportsUpdate"] is True
    assert resp["SupportsLocks"] is True
    assert resp["SupportsGetLock"] is True
    assert resp["UserCanWrite"] is True
    assert resp["UserCanNotWrite"] is False


def test_file_info_response_user_can_not_write_override():
    """CheckFileInfo allows UserCanNotWrite override for permission changes."""
    resp = file_info_response(
        name="doc.docx",
        version="1.0",
        size=1024,
        owner="owner1",
        user_id="user1",
        user_name="User One",
        last_modified=1234567890,
        user_can_not_write=True,
    )
    assert resp["UserCanNotWrite"] is True


# ---------------------------------------------------------------------------
# 5. Empty response (empty_wopi_response)
# ---------------------------------------------------------------------------


def test_empty_wopi_response_is_dict():
    """Empty WOPI response is a dict for JSON serialization."""
    resp = empty_wopi_response()
    assert isinstance(resp, dict)


def test_empty_wopi_response_is_empty():
    """Empty WOPI response contains no keys (minimal body)."""
    resp = empty_wopi_response()
    assert resp == {}


# ---------------------------------------------------------------------------
# 6. Header constants (WOPI spec compliance)
# ---------------------------------------------------------------------------


def test_lock_header_constant():
    """LOCK_HEADER matches the WOPI spec header name."""
    assert LOCK_HEADER == "X-WOPI-Lock"


def test_old_lock_header_constant():
    """OLD_LOCK_HEADER matches the legacy WOPI spec header name."""
    assert OLD_LOCK_HEADER == "X-WOPI-OldLock"


def test_override_header_constant():
    """OVERRIDE_HEADER matches the WOPI spec header name for Override mode."""
    assert OVERRIDE_HEADER == "X-WOPI-Override"


# ---------------------------------------------------------------------------
# 7. Unicode / internationalisation edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "文件.docx",  # Chinese
        "файл.docx",  # Russian (Cyrillic)
        "ファイル.docx",  # Japanese (Kana)
        "αρχείο.docx",  # Greek
        "ملف.docx",  # Arabic
        "文件_файл_ファイル.docx",  # Mixed script
        "test_üñíçödé.docx",  # Latin-1 supplement
        "testemoji😀.docx",  # Emoji
    ],
)
def test_file_info_response_unicode_name(name: str):
    """CheckFileInfo payload correctly handles Unicode filenames."""
    resp = file_info_response(
        name=name,
        version="1.0",
        size=1024,
        owner="owner1",
        user_id="user1",
        user_name="User One",
        last_modified=1234567890,
    )
    assert resp["BaseFileName"] == name


@pytest.mark.parametrize(
    "user_name",
    [
        "用户",  # Chinese
        "Иван",  # Russian
        "山田太郎",  # Japanese
        "Μαρία",  # Greek
        "محمد",  # Arabic
        "José García",  # Latin-1
        "Test User 🎉",  # Emoji
    ],
)
def test_file_info_response_unicode_user_name(user_name: str):
    """CheckFileInfo payload correctly handles Unicode user names."""
    resp = file_info_response(
        name="doc.docx",
        version="1.0",
        size=1024,
        owner="owner1",
        user_id="user1",
        user_name=user_name,
        last_modified=1234567890,
    )
    assert resp["UserFriendlyName"] == user_name