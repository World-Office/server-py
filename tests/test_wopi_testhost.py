"""Unit tests for the mock WOPI host (src/wopi/testhost.py) protocol surface.

Paradigm: **Unit tests** that drive the FastAPI endpoint coroutines directly
with constructed ``starlette.requests.Request`` objects via ``asyncio.run``.
This exercises the exact code the real E2E harness runs (check_file_info →
get_file → put_file → lock_ops → host_open) without spinning up uvicorn or
requiring an HTTP client, so the suite stays deterministic and self-contained
(no network, no sleeps, no time-of-day dependence).

Every endpoint the docserver relies on as a WOPI *client* is pinned here so a
broken mock never silently "passes" an integration run by accident:

* ``/_host/files``                          — create a file, mints id + token
* ``check_file_info`` (GET /wopi/files/{id}) — WOPI metadata + auth gate
* ``get_file`` (GET .../contents)           — raw bytes + content-type
* ``put_file`` (POST .../contents)          — override header, lock, size cap
* ``lock_ops`` (POST /wopi/files/{id})      — Lock / Unlock / RefreshLock / GetLock
* ``host_open`` (GET /open/{id})            — host-launch redirect into editor
* ``_content_type``                         — extension -> MIME mapping
* ``reset_store``                           — store isolation between tests
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from starlette.requests import Request

from src.wopi.testhost import (
    CONTENT_TYPES,
    MAX_FILE_SIZE,
    _content_type,
    check_file_info,
    get_file,
    host_create_file,
    host_open,
    lock_ops,
    put_file,
    reset_store,
)

# ---------------------------------------------------------------------------
# In-process harness
# ---------------------------------------------------------------------------

def _req(
    path: str = "/",
    query: str = "",
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> Request:
    """Build a real starlette Request (no HTTP client / network needed)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def _clean_store():
    """Isolate the module-level mock-host store between tests."""
    reset_store()
    yield
    reset_store()


def _create(name: str = "hello.docx", data: bytes = b"hello world", doc_id: str | None = None) -> dict:
    """Seed a file through the host-only helper; return {id, access_token, name}."""
    payload: dict = {"name": name}
    if data is not None:
        payload["data"] = base64.b64encode(data).decode()
    if doc_id is not None:
        payload["id"] = doc_id
    res = asyncio.run(
        host_create_file(
            _req(
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps(payload).encode(),
            )
        )
    )
    assert res.status_code == 200
    return json.loads(res.body)


def _json(res) -> dict:
    return json.loads(res.body)


# ---------------------------------------------------------------------------
# Host-only helper: file creation + token minting
# ---------------------------------------------------------------------------

def test_create_file_mints_id_and_token():
    """POST /_host/files creates a doc and returns a usable id + access_token."""
    res = asyncio.run(
        host_create_file(
            _req(
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"name": "report.docx"}).encode(),
            )
        )
    )
    assert res.status_code == 200
    body = _json(res)
    assert body["name"] == "report.docx"
    assert body["id"].startswith("host-")
    assert body["access_token"].startswith("tok-")

    # the minted token must authenticate against the created doc
    cfi = asyncio.run(
        check_file_info(body["id"], _req(query=f"access_token={body['access_token']}"))
    )
    assert cfi.status_code == 200


def test_create_file_accepts_base64_data():
    """Data passed as base64 in the create payload is decoded and stored."""
    data = b"\x00\x01binary payload\xff"
    body = _create(name="blob.bin", data=data)
    res = asyncio.run(
        get_file(body["id"], _req(query=f"access_token={body['access_token']}"))
    )
    assert res.status_code == 200
    assert res.body == data


def test_create_file_keeps_requested_id():
    """An explicit id in the create payload is honoured (deterministic hosts)."""
    body = _create(name="x.txt", doc_id="my-doc")
    assert body["id"] == "my-doc"


# ---------------------------------------------------------------------------
# CheckFileInfo (GET /wopi/files/{id})
# ---------------------------------------------------------------------------

def test_check_file_info_returns_wopi_metadata():
    """CheckFileInfo exposes the WOPI discovery fields the docserver consumes."""
    body = _create(name="hello.docx", data=b"0123456789")
    res = asyncio.run(
        check_file_info(body["id"], _req(query=f"access_token={body['access_token']}"))
    )
    assert res.status_code == 200
    info = _json(res)
    assert info["BaseFileName"] == "hello.docx"
    assert info["Size"] == 10
    assert info["Version"] == "10"
    assert info["OwnerId"] == body["id"]
    assert info["UserId"] == body["id"]
    assert info["UserName"] == "Mock Host User"
    assert info["SupportsLocks"] is True
    assert info["SupportsUpdate"] is True
    assert info["SupportsGetLock"] is True
    # LastModifiedTime is an ISO-ish timestamp (shape only, no TOD dependence)
    assert info["LastModifiedTime"].startswith("20")


def test_check_file_info_requires_access_token():
    """The mock host 404s unknown docs and token-less requests (fail closed)."""
    body = _create()
    # no access_token at all
    assert asyncio.run(check_file_info(body["id"], _req())).status_code == 404
    # unknown doc id with a token
    assert asyncio.run(
        check_file_info("ghost", _req(query="access_token=tok-x"))
    ).status_code == 404
    # known doc + token -> 200
    assert asyncio.run(
        check_file_info(body["id"], _req(query=f"access_token={body['access_token']}"))
    ).status_code == 200


def test_check_file_info_accepts_any_nonempty_token():
    """The mock host accepts any non-empty access_token (mock posture)."""
    body = _create()
    for tok in ("anything", "abc", "123"):
        res = asyncio.run(
            check_file_info(body["id"], _req(query=f"access_token={tok}"))
        )
        assert res.status_code == 200
    # empty token is treated as absent
    assert asyncio.run(
        check_file_info(body["id"], _req(query="access_token="))
    ).status_code == 404


# ---------------------------------------------------------------------------
# GetFile (GET /wopi/files/{id}/contents)
# ---------------------------------------------------------------------------

def test_get_file_returns_bytes_with_mime_type():
    """GetFile returns the raw stored bytes typed by the file extension."""
    body = _create(name="notes.odt", data=b"%PDF-fake-odt")
    res = asyncio.run(
        get_file(body["id"], _req(query=f"access_token={body['access_token']}"))
    )
    assert res.status_code == 200
    assert res.body == b"%PDF-fake-odt"
    assert res.headers["content-type"] == CONTENT_TYPES[".odt"]


def test_get_file_unknown_doc_404():
    """GetFile on a missing doc is a 404, not a crash."""
    res = asyncio.run(get_file("nope", _req(query="access_token=tok-x")))
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# PutFile (POST /wopi/files/{id}/contents)
# ---------------------------------------------------------------------------

def test_put_file_replaces_content_roundtrip():
    """PutFile with X-WOPI-Override: PUT persists bytes readable by GetFile."""
    body = _create(name="doc.bin", data=b"old")
    url = f"access_token={body['access_token']}"
    res = asyncio.run(
        put_file(
            body["id"],
            _req(
                method="POST",
                query=url,
                headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": ""},
                body=b"new data!",
            ),
        )
    )
    assert res.status_code == 200
    assert _json(res) == {"ok": True, "size": 9}

    got = asyncio.run(get_file(body["id"], _req(query=url)))
    assert got.body == b"new data!"

    # CheckFileInfo Size tracks the new payload
    info = _json(
        asyncio.run(check_file_info(body["id"], _req(query=url)))
    )
    assert info["Size"] == 9


def test_put_file_requires_override_header():
    """A plain POST without X-WOPI-Override: PUT is rejected (spec posture)."""
    body = _create()
    res = asyncio.run(
        put_file(
            body["id"],
            _req(method="POST", query=f"access_token={body['access_token']}", body=b"x"),
        )
    )
    assert res.status_code == 400
    assert "X-WOPI-Override" in _json(res)["error"]


def test_put_file_lock_conflict_returns_409():
    """PutFile against a foreign lock returns 409 echoing the current lock."""
    body = _create(name="locked.txt", data=b"a")
    auth = f"access_token={body['access_token']}"
    contents = _req(
        method="POST",
        query=auth,
        headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "WRONG"},
        body=b"evil",
    )

    # seed a lock via the WOPI LOCK override
    assert asyncio.run(
        lock_ops(
            body["id"],
            _req(
                method="POST",
                query=auth,
                headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "LOCK-42"},
            ),
        )
    ).status_code == 200

    # foreign lock -> 409 with the real lock echoed, data untouched
    res = asyncio.run(put_file(body["id"], contents))
    assert res.status_code == 409
    assert res.headers["X-WOPI-Lock"] == "LOCK-42"
    assert asyncio.run(get_file(body["id"], _req(query=auth))).body == b"a"

    # correct lock -> 200 and data replaced
    res = asyncio.run(
        put_file(
            body["id"],
            _req(
                method="POST",
                query=auth,
                headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "LOCK-42"},
                body=b"b",
            ),
        )
    )
    assert res.status_code == 200
    assert asyncio.run(get_file(body["id"], _req(query=auth))).body == b"b"


def test_put_file_enforces_size_limit():
    """Payloads over MAX_FILE_SIZE are rejected with 413."""
    body = _create()
    auth = f"access_token={body['access_token']}"
    res = asyncio.run(
        put_file(
            body["id"],
            _req(
                method="POST",
                query=auth,
                headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": ""},
                body=b"\x00" * (MAX_FILE_SIZE + 1),
            ),
        )
    )
    assert res.status_code == 413
    # existing data untouched
    assert asyncio.run(get_file(body["id"], _req(query=auth))).body == b"hello world"


# ---------------------------------------------------------------------------
# Lock operations (POST /wopi/files/{id}, X-WOPI-Override)
# ---------------------------------------------------------------------------

def test_lock_unlock_getlock_cycle():
    """LOCK -> GET_LOCK echo, UNLOCK clears, GET_LOCK then reports unlocked."""
    body = _create()
    auth = f"access_token={body['access_token']}"

    res = asyncio.run(
        lock_ops(
            body["id"],
            _req(method="POST", query=auth, headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L1"}),
        )
    )
    assert res.status_code == 200
    assert res.headers["X-WOPI-Lock"] == "L1"

    res = asyncio.run(
        lock_ops(body["id"], _req(method="POST", query=auth, headers={"X-WOPI-Override": "GET_LOCK"}))
    )
    assert res.status_code == 200
    assert res.headers["X-WOPI-Lock"] == "L1"

    res = asyncio.run(
        lock_ops(
            body["id"],
            _req(method="POST", query=auth, headers={"X-WOPI-Override": "UNLOCK", "X-WOPI-Lock": "L1"}),
        )
    )
    assert res.status_code == 200

    # unlocked sentinel is a single space per WOPI convention
    res = asyncio.run(
        lock_ops(body["id"], _req(method="POST", query=auth, headers={"X-WOPI-Override": "GET_LOCK"}))
    )
    assert res.status_code == 200
    assert res.headers["X-WOPI-Lock"] == " "


def test_lock_first_writer_wins():
    """A foreign LOCK is refused with 409 + the current lock echoed."""
    body = _create()
    auth = f"access_token={body['access_token']}"

    def op(override: str, lock: str):
        return asyncio.run(
            lock_ops(
                body["id"],
                _req(
                    method="POST",
                    query=auth,
                    headers={"X-WOPI-Override": override, "X-WOPI-Lock": lock},
                ),
            )
        )

    assert op("LOCK", "MINE").status_code == 200

    res = op("LOCK", "THEIRS")
    assert res.status_code == 409
    assert res.headers["X-WOPI-Lock"] == "MINE"

    # refresh by the owner keeps the lock alive
    assert op("REFRESH_LOCK", "MINE").status_code == 200

    # refresh / unlock by a stranger is a 409
    assert op("REFRESH_LOCK", "THEIRS").status_code == 409
    assert op("UNLOCK", "THEIRS").status_code == 409


def test_lock_unknown_override_rejected():
    """An unsupported X-WOPI-Override value is a 400."""
    body = _create()
    res = asyncio.run(
        lock_ops(
            body["id"],
            _req(
                method="POST",
                query=f"access_token={body['access_token']}",
                headers={"X-WOPI-Override": "DELETE"},
            ),
        )
    )
    assert res.status_code == 400


def test_lock_ops_require_auth_like_other_endpoints():
    """Lock overrides on a token-less / unknown doc stay fail-closed (404)."""
    body = _create()
    # no token -> 404
    assert asyncio.run(
        lock_ops(
            body["id"],
            _req(method="POST", headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L"}),
        )
    ).status_code == 404
    # unknown doc with token -> 404
    assert asyncio.run(
        lock_ops("ghost", _req(method="POST", query="access_token=tok-x", headers={"X-WOPI-Override": "GET_LOCK"}))
    ).status_code == 404


# ---------------------------------------------------------------------------
# Host launch redirect (GET /open/{id})
# ---------------------------------------------------------------------------

def test_open_redirects_into_docserver_editor():
    """Host launch redirects to the docserver editor with WOPISrc + token."""
    body = _create()
    res = asyncio.run(
        host_open(body["id"], _req(query=f"access_token={body['access_token']}"))
    )
    assert res.status_code == 307
    loc = res.headers["location"]
    assert loc.startswith("http://localhost:8000/editor/")
    assert body["id"] in loc
    assert f"access_token={body['access_token']}" in loc
    # WOPISrc must carry a full WOPI file URL back to the mock host
    assert f"WOPISrc=http://testserver/wopi/files/{body['id']}" in loc


def test_open_redirect_honours_doc_server_param():
    """?doc_server= overrides the default editor base URL."""
    body = _create()
    res = asyncio.run(
        host_open(
            body["id"],
            _req(query="access_token=tok&doc_server=http://editor:9000"),
        )
    )
    assert res.status_code == 307
    assert res.headers["location"].startswith("http://editor:9000/editor/")


# ---------------------------------------------------------------------------
# _content_type mapping (pure helper)
# ---------------------------------------------------------------------------

def test_content_type_maps_known_extensions():
    """Known editor formats map to their MIME types, case-insensitively."""
    assert _content_type("hello.docx") == CONTENT_TYPES[".docx"]
    assert _content_type("notes.odt") == CONTENT_TYPES[".odt"]
    assert _content_type("readme.md") == CONTENT_TYPES[".md"]
    assert _content_type("plain.txt") == CONTENT_TYPES[".txt"]
    assert _content_type("UPPER.DOCX") == CONTENT_TYPES[".docx"]


def test_content_type_falls_back_to_octet_stream():
    """Unknown / absent extensions degrade to application/octet-stream."""
    assert _content_type("archive.tar.gz") == "application/octet-stream"
    assert _content_type("noextension") == "application/octet-stream"
    assert _content_type("a.") == "application/octet-stream"
