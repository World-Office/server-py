"""Mock WOPI host — emulates an OpenCloud / Nextcloud WOPI host for local E2E.

This is a self-contained FastAPI app that plays the WOPI **host** role so the
opencloud-docserver can be exercised end-to-end without a full OpenCloud or
Nextcloud deployment. The docserver talks to it as a WOPI **client** via
``RemoteWopiClient`` (CheckFileInfo, GetFile, PutFile, Lock/Unlock/GetLock).

Endpoints implemented (per WOPI spec, matching ``RemoteWopiClient``):
    GET  /wopi/files/{id}                  -- CheckFileInfo
    GET  /wopi/files/{id}/contents         -- GetFile
    POST /wopi/files/{id}/contents         -- PutFile (X-WOPI-Override: PUT)
    POST /wopi/files/{id}                  -- Lock/Unlock/RefreshLock/GetLock
                                            (X-WOPI-Override header)

Host-only (non-WOPI) helpers used by tests / manual runs:
    POST /_host/files                      -- create a file, returns {id, access_token}
    GET /open/{id}                         -- redirect to the docserver editor URL

Run:  uvicorn src.wopi.testhost:app --port 8731
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

router = APIRouter()

CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

# In-memory file store: id -> {"name", "data", "lock"}
_HOST_STORE: dict[str, dict] = {}

# access_token -> doc_id (the host issues tokens when creating files)
_TOKENS: dict[str, str] = {}

MAX_FILE_SIZE = 128 * 1024 * 1024


def _content_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return CONTENT_TYPES.get(f".{ext}", "application/octet-stream")


def _ok_token(doc_id: str, request: Request) -> bool:
    """Accept any non-empty access_token (this is a mock host)."""
    tok = request.query_params.get("access_token", "")
    return bool(tok)


# ----------------------------------------------------------------------
# Host-only helpers
# ----------------------------------------------------------------------

@router.post("/_host/files")
async def host_create_file(request: Request) -> JSONResponse:
    """Create a file in the mock host store. Body: {"name", "data"(b64 optional)}.

    Returns the doc id and an access_token the docserver should use as a WOPI
    client. In a real host these would be issued via the WOPI handshake; here
    we mint them directly.
    """
    import base64

    payload = await request.json()
    name = payload.get("name", "document.docx")
    data = payload.get("data")
    if isinstance(data, str):
        data = base64.b64decode(data)
    doc_id = payload.get("id") or f"host-{uuid.uuid4().hex[:12]}"
    token = f"tok-{uuid.uuid4().hex}"
    _HOST_STORE[doc_id] = {"name": name, "data": data or b"", "lock": ""}
    _TOKENS[token] = doc_id
    return JSONResponse({"id": doc_id, "access_token": token, "name": name})


@router.get("/open/{doc_id}")
async def host_open(doc_id: str, request: Request) -> RedirectResponse:
    """Redirect to the docserver editor URL for this file (host launch)."""
    doc_server = request.query_params.get("doc_server") or "http://localhost:8000"
    token = request.query_params.get("access_token", "")
    wopi_src = f"{request.base_url.scheme}://{request.base_url.netloc}/wopi/files/{doc_id}"
    url = f"{doc_server}/editor/{doc_id}?access_token={token}&WOPISrc={wopi_src}"
    return RedirectResponse(url)


# ----------------------------------------------------------------------
# WOPI Server API (host side)
# ----------------------------------------------------------------------

@router.get("/wopi/files/{doc_id}")
async def check_file_info(doc_id: str, request: Request) -> JSONResponse:
    doc = _HOST_STORE.get(doc_id)
    if doc is None or not _ok_token(doc_id, request):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(
        {
            "BaseFileName": doc["name"],
            "Size": len(doc["data"]),
            "OwnerId": doc_id,
            "UserId": doc_id,
            "UserName": "Mock Host User",
            "Version": str(len(doc["data"])),
            "LastModifiedTime": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time())),
            "SupportsLocks": True,
            "SupportsUpdate": True,
            "SupportsGetLock": True,
        }
    )


@router.get("/wopi/files/{doc_id}/contents")
async def get_file(doc_id: str, request: Request) -> Response:
    doc = _HOST_STORE.get(doc_id)
    if doc is None or not _ok_token(doc_id, request):
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=doc["data"], media_type=_content_type(doc["name"]))


@router.post("/wopi/files/{doc_id}/contents")
async def put_file(doc_id: str, request: Request) -> JSONResponse:
    doc = _HOST_STORE.get(doc_id)
    if doc is None or not _ok_token(doc_id, request):
        return JSONResponse({"error": "not found"}, status_code=404)
    if request.headers.get("X-WOPI-Override", "").upper() != "PUT":
        return JSONResponse({"error": "expected X-WOPI-Override: PUT"}, status_code=400)
    # Honour the lock the docserver presents.
    lock = request.headers.get("X-WOPI-Lock", "")
    if doc["lock"] and lock != doc["lock"]:
        return JSONResponse(
            {"error": "lock mismatch"}, status_code=409,
            headers={"X-WOPI-Lock": doc["lock"]},
        )
    body = await request.body()
    if len(body) > MAX_FILE_SIZE:
        return JSONResponse({"error": "file too large"}, status_code=413)
    doc["data"] = body
    return JSONResponse({"ok": True, "size": len(body)}, headers={"X-WOPI-Lock": doc["lock"] or ""})


@router.post("/wopi/files/{doc_id}")
async def lock_ops(doc_id: str, request: Request) -> JSONResponse:
    """Lock / Unlock / RefreshLock / GetLock via X-WOPI-Override."""
    doc = _HOST_STORE.get(doc_id)
    if doc is None or not _ok_token(doc_id, request):
        return JSONResponse({"error": "not found"}, status_code=404)
    override = request.headers.get("X-WOPI-Override", "").upper()
    lock = request.headers.get("X-WOPI-Lock", "")

    if override == "GET_LOCK":
        return JSONResponse({}, headers={"X-WOPI-Lock": doc["lock"] or " "})

    if override == "LOCK":
        if doc["lock"] and doc["lock"] != lock:
            return JSONResponse(
                {"error": "locked"}, status_code=409, headers={"X-WOPI-Lock": doc["lock"]}
            )
        doc["lock"] = lock
        return JSONResponse({}, headers={"X-WOPI-Lock": lock})

    if override == "REFRESH_LOCK":
        if doc["lock"] and doc["lock"] != lock:
            return JSONResponse(
                {"error": "lock mismatch"}, status_code=409,
                headers={"X-WOPI-Lock": doc["lock"]},
            )
        doc["lock"] = lock
        return JSONResponse({}, headers={"X-WOPI-Lock": lock})

    if override == "UNLOCK":
        if doc["lock"] and doc["lock"] != lock:
            return JSONResponse(
                {"error": "lock mismatch"}, status_code=409,
                headers={"X-WOPI-Lock": doc["lock"]},
            )
        doc["lock"] = ""
        return JSONResponse({})

    return JSONResponse({"error": f"unknown override: {override}"}, status_code=400)


app = FastAPI(title="mock-wopi-host")
app.include_router(router)


def reset_store() -> None:
    """Clear all mock-host state (used by tests)."""
    _HOST_STORE.clear()
    _TOKENS.clear()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8731)
