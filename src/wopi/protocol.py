"""WOPI protocol helpers: response shapes, lock headers, constants.

Follows the WOPI (Web Application Open Platform Interface) spec
https://learn.microsoft.com/en-us/microsoft-365/cloud-storage-partner-program/rest/
"""

from __future__ import annotations

# Lock header name used on PutFile / lock operations
LOCK_HEADER = "X-WOPI-Lock"
OLD_LOCK_HEADER = "X-WOPI-OldLock"
OVERRIDE_HEADER = "X-WOPI-Override"

# Response codes defined by the WOPI spec
HTTP_FILE_NOT_FOUND = 404
HTTP_LOCK_MISMATCH = 409
HTTP_LOCK_CONFLICT = 409
HTTP_FILE_TOO_LARGE = 413
HTTP_METHOD_NOT_IMPLEMENTED = 501


class WopiError(Exception):
    """Represents a WOPI-protocol-level failure with an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def empty_wopi_response() -> dict:
    """Return an empty WOPI JSON body (used for most calls)."""
    return {}


def file_info_response(
    *,
    name: str,
    version: str,
    size: int,
    owner: str,
    user_id: str,
    user_name: str,
    last_modified: int,
    read_only: bool = False,
    supports_update: bool = True,
    supports_locks: bool = True,
    supports_get_lock: bool = True,
    user_can_write: bool = True,
    user_can_not_write: bool = False,
) -> dict:
    """Build the CheckFileInfo JSON payload per the WOPI spec."""
    return {
        "BaseFileName": name,
        "OwnerId": owner,
        "Size": size,
        "UserId": user_id,
        "UserFriendlyName": user_name,
        "Version": version,
        "LastModifiedTime": last_modified,
        "ReadOnly": read_only,
        "SupportsUpdate": supports_update,
        "SupportsLocks": supports_locks,
        "SupportsGetLock": supports_get_lock,
        "UserCanWrite": user_can_write,
        "UserCanNotWrite": user_can_not_write,
        "UserCanRename": True,
        "SupportsRename": False,
    }


def lock_mismatch_error(expected: str, actual: str) -> WopiError:
    """Return the standard WOPI lock-mismatch error with X-WOPI-Lock."""
    return WopiError(HTTP_LOCK_MISMATCH, f"Lock mismatch: expected {expected!r}, got {actual!r}")


def invalid_doc_id(doc_id: str) -> bool:
    """True when a WOPI/editor file id must be rejected as path-traversal.

    Content bytes live at ``{content_dir}/{doc_id}.bin``, so an id containing
    path separators addresses a file outside the store's content directory.
    An attacker can smuggle separators into a URL path param URI-encoded
    (``%2F``, ``%5C``) — FastAPI/Starlette decodes the segment before the
    handler runs, turning e.g. ``..%2F..%2Fsecret`` into a doc id of
    ``../../secret``. Opaque host ids never legitimately contain separators
    or traversal segments, so reject them outright.

    Shared by the WOPI host router and the editor API (both reach the
    content directory through ``DocumentStore.content_path``).
    """
    if not doc_id:
        return True
    if len(doc_id) > 128:
        # Opaque host ids are short; a longer id would overflow NAME_MAX in
        # the content filename (``{doc_id}.bin``) after the final ".." check
        # (identifier-1: 128 chars + ".bin" = 132 < 255) — reject outright.
        return True
    if "/" in doc_id or "\\" in doc_id or "\x00" in doc_id:
        return True
    if doc_id in {".", ".."}:
        return True
    # No separator can remain at this point, so a bare ".." substring can no
    # longer change directory resolution — reject it anyway: opaque ids never
    # contain it and it keeps the contract obvious at every call site.
    if ".." in doc_id:
        return True
    return False
