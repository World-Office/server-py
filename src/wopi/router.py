"""WOPI host router: CheckFileInfo, GetFile, PutFile, Lock/Unlock.

This server plays WOPI **host** when serving documents from its local
SQLite store, and WOPI **client** when launched by OCIS with an access
token (see `src/editor/session.py` for the client-side forwarding).

Endpoints (per WOPI spec):
    GET  /wopi/files/{id}             -- CheckFileInfo
    GET  /wopi/files/{id}/contents    -- GetFile
    POST /wopi/files/{id}/contents    -- PutFile
    POST /wopi/files/{id}/lock        -- Lock
    POST /wopi/files/{id}/unlock      -- Unlock
    POST /wopi/files/{id}/refreshlock -- RefreshLock
    POST /wopi/files/{id}/getlock     -- GetLock
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..lib.store import DocumentStore
from .protocol import (
    LOCK_HEADER,
    WopiError,
    file_info_response,
    invalid_doc_id,
    lock_mismatch_error,
)

router = APIRouter()

# Content types by extension (kept deliberately small).
CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

MAX_FILE_SIZE = 128 * 1024 * 1024  # 128 MiB


def _invalid_doc_id(doc_id: str) -> bool:
    """True when a WOPI file id must be rejected as a path-traversal attempt.

    Re-exported from :mod:`wopi.protocol`, which shares the predicate with
    the editor API (both reach the content directory through
    ``DocumentStore.content_path``).
    """
    return invalid_doc_id(doc_id)


def _wopi_invalid_id_response() -> JSONResponse:
    """400 response for a file id rejected by :func:`_invalid_doc_id`."""
    return JSONResponse(status_code=400, content={"error": "Invalid file id"})


def _content_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return CONTENT_TYPES.get(f".{ext}", "application/octet-stream")


def _wopi_error_response(err: WopiError, lock: str = "") -> JSONResponse:
    headers = {LOCK_HEADER: lock} if lock else {}
    return JSONResponse(status_code=err.status, content={"error": err.message}, headers=headers)


def _lock_error(store: DocumentStore, doc_id: str, expected: str) -> JSONResponse:
    current = store.get_lock(doc_id)
    return _wopi_error_response(lock_mismatch_error(expected, current), lock=current)


def _store_of(request: Request) -> DocumentStore:
    return request.app.state.store


def _check_file(request: Request, doc_id: str) -> JSONResponse:
    store = _store_of(request)
    doc = store.get(doc_id)
    if doc is None:
        return _wopi_error_response(WopiError(404, f"File not found: {doc_id}"))
    return JSONResponse(
        file_info_response(
            name=doc["name"],
            version=str(doc["updated_at"]),
            size=doc["size"],
            owner=doc["id"],
            user_id=doc["id"],
            user_name="Local User",
            last_modified=int(doc["updated_at"]),
        )
    )


@router.get("/wopi/files/{doc_id}")
async def check_file_info(doc_id: str, request: Request) -> JSONResponse:
    """WOPI CheckFileInfo: return document metadata as JSON."""
    if _invalid_doc_id(doc_id):
        return _wopi_invalid_id_response()
    return _check_file(request, doc_id)


@router.get("/wopi/files/{doc_id}/contents")
async def get_file(doc_id: str, request: Request) -> Response:
    """WOPI GetFile: return the raw document bytes."""
    if _invalid_doc_id(doc_id):
        return _wopi_invalid_id_response()
    store = _store_of(request)
    doc = store.get(doc_id)
    if doc is None:
        return _wopi_error_response(WopiError(404, f"File not found: {doc_id}"))
    data = store.get_content(doc_id)
    if data is None:
        return _wopi_error_response(WopiError(404, f"Content missing for {doc_id}"))
    return Response(
        content=data,
        media_type=_content_type(doc["name"]),
        headers={"X-WOPI-ItemVersion": str(doc["updated_at"])},
    )


@router.post("/wopi/files/{doc_id}/contents")
async def put_file(doc_id: str, request: Request) -> JSONResponse:
    """WOPI PutFile: store new content, honouring the current lock."""
    if _invalid_doc_id(doc_id):
        return _wopi_invalid_id_response()
    store = _store_of(request)
    doc = store.get(doc_id)
    if doc is None:
        return _wopi_error_response(WopiError(404, f"File not found: {doc_id}"))

    lock = request.headers.get(LOCK_HEADER, "")
    current_lock = store.get_lock(doc_id)
    if current_lock and lock != current_lock:
        return _lock_error(store, doc_id, current_lock)

    body = await request.body()
    if len(body) > MAX_FILE_SIZE:
        return _wopi_error_response(WopiError(413, "File too large"))

    store.put_content(doc_id, body)
    return JSONResponse(
        {"ok": True, "size": len(body)},
        headers={"X-WOPI-Lock": store.get_lock(doc_id) or ""},
    )


def _require_lock_header(request: Request, store: DocumentStore, doc_id: str) -> str | None:
    lock = request.headers.get(LOCK_HEADER, "")
    current = store.get_lock(doc_id)
    if current and lock != current:
        return _lock_error(store, doc_id, current)
    if lock == "" and current:
        return _lock_error(store, doc_id, current)
    return None


@router.post("/wopi/files/{doc_id}/lock")
async def lock_file(doc_id: str, request: Request) -> JSONResponse:
    """WOPI Lock: acquire a lock unless another lock is held.

    Lock contention follows first-writer-wins: exactly one of several
    simultaneous Lock requests succeeds, and every loser gets a 409 whose
    ``X-WOPI-Lock`` header echoes the winner's token (per the WOPI spec) so
    clients can adopt or back off. A Lock with the same token as the current
    lock is a refresh and keeps the lock; an empty lock token is rejected
    (WOPI lock tokens MUST be non-empty).
    """
    if _invalid_doc_id(doc_id):
        return _wopi_invalid_id_response()
    store = _store_of(request)
    if store.get(doc_id) is None:
        return _wopi_error_response(WopiError(404, f"File not found: {doc_id}"))
    lock = request.headers.get(LOCK_HEADER, "")
    if not lock:
        return _wopi_error_response(WopiError(400, "Lock token must be non-empty"))

    current = store.get_lock(doc_id)

    if current:
        # Lock refresh if same token, otherwise conflict
        if lock == current:
            # The spec requires Lock responses to echo the lock token.
            return JSONResponse({}, headers={LOCK_HEADER: lock})
        return _lock_error(store, doc_id, current)

    user = request.query_params.get("user", "")
    store.set_lock(doc_id, lock, user)
    return JSONResponse({}, headers={LOCK_HEADER: lock})


@router.post("/wopi/files/{doc_id}/unlock")
async def unlock_file(doc_id: str, request: Request) -> JSONResponse:
    """WOPI Unlock: release the lock if the token matches."""
    if _invalid_doc_id(doc_id):
        return _wopi_invalid_id_response()
    store = _store_of(request)
    if store.get(doc_id) is None:
        return _wopi_error_response(WopiError(404, f"File not found: {doc_id}"))
    lock = request.headers.get(LOCK_HEADER, "")
    if store.get_lock(doc_id) and store.get_lock(doc_id) != lock:
        return _lock_error(store, doc_id, store.get_lock(doc_id))
    store.release_lock(doc_id)
    return JSONResponse({})


@router.post("/wopi/files/{doc_id}/refreshlock")
async def refresh_lock(doc_id: str, request: Request) -> JSONResponse:
    """WOPI RefreshLock: extend the lock lease."""
    if _invalid_doc_id(doc_id):
        return _wopi_invalid_id_response()
    store = _store_of(request)
    if store.get(doc_id) is None:
        return _wopi_error_response(WopiError(404, f"File not found: {doc_id}"))
    lock = request.headers.get(LOCK_HEADER, "")
    current = store.get_lock(doc_id)
    if current and lock != current:
        return _lock_error(store, doc_id, current)
    store.set_lock(doc_id, lock, store.get(doc_id).get("lock_user", ""))
    return JSONResponse({}, headers={LOCK_HEADER: lock})


@router.post("/wopi/files/{doc_id}/getlock")
async def get_lock(doc_id: str, request: Request) -> JSONResponse:
    """WOPI GetLock: return the current lock token."""
    if _invalid_doc_id(doc_id):
        return _wopi_invalid_id_response()
    store = _store_of(request)
    if store.get(doc_id) is None:
        return _wopi_error_response(WopiError(404, f"File not found: {doc_id}"))
    lock = store.get_lock(doc_id)
    return JSONResponse({}, headers={LOCK_HEADER: lock or " "})
