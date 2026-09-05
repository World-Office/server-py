"""WOPI router edge cases: MAX_FILE_SIZE, foreign lock ops, GetLock (UNIT).

Paradigm: **Unit tests** covering router-level edge cases that the protocol
layer (``wopi.protocol``) doesn't reach: the ``wopi.router`` HTTP surface.

Scenarios under test:

1. **MAX_FILE_SIZE boundary** — PutFile honours the 128 MiB cap: bodies above
   the limit get 413 and leave stored content untouched; bodies at/under the
   limit (including empty) are accepted and persisted.
2. **Foreign-lock ops** — every lock-bearing operation rejects a mismatched
   token: PutFile, Lock (first-writer-wins 409 echoing the winner's token),
   Unlock, RefreshLock (409 on foreign token, refresh on same token, acquire
   on unlocked file). Empty Lock tokens are rejected outright (400).
3. **GetLock shape** — GetLock returns an empty JSON body with the current
   token in ``X-WOPI-Lock`` (or a single space when unlocked), and 404 for
   unknown files.

Deterministic: no network, no sleeps, no time-of-day dependence. The size
boundary is exercised by monkeypatching ``MAX_FILE_SIZE`` down to a few KB so
no test transfers a real 128 MiB payload.
"""

from __future__ import annotations

import pytest
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi import router as wopi_router_module
from src.wopi.router import MAX_FILE_SIZE, router as wopi_router


# -----------------------------------------------------------------------------
# Shared app builder (kept in sync with tests/test_wopi.py::_make_app)
# -----------------------------------------------------------------------------


def _make_app(tmp_path):
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")
    store = DocumentStore(db, content)
    cfg = Config(database=db, content_dir=content, jwt_secret="test-secret")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.sessions = SessionRegistry()
        app.state.config = cfg
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(wopi_router)
    app.include_router(editor_router)
    return app, store


@pytest.fixture
def client(tmp_path):
    """TestClient with lifespan running; backing store on ``client.test_store``."""
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _seed(client, doc_id="doc1", name="doc.docx", data=b"original"):
    store = client.test_store  # type: ignore[attr-defined]
    store.init(doc_id, name)
    store.put_content(doc_id, data)


# -----------------------------------------------------------------------------
# 1. MAX_FILE_SIZE boundary (413 oversize)
# -----------------------------------------------------------------------------


def test_put_file_413_on_oversize(client, monkeypatch):
    """PutFile returns 413 when the body exceeds MAX_FILE_SIZE and leaves the
    stored content untouched (no partial/oversized write)."""
    monkeypatch.setattr(wopi_router_module, "MAX_FILE_SIZE", 1024)
    _seed(client, data=b"original")

    # One byte over the (monkeypatched) limit.
    oversized = b"x" * (1024 + 1)
    res = client.post("/wopi/files/doc1/contents", content=oversized)

    assert res.status_code == 413
    assert "File too large" in res.json()["error"]
    # The oversized write must not have reached the store.
    assert client.test_store.get_content("doc1") == b"original"  # type: ignore[attr-defined]


def test_put_file_accepts_exactly_max_size(client, monkeypatch):
    """PutFile accepts a body of exactly MAX_FILE_SIZE bytes (boundary)."""
    monkeypatch.setattr(wopi_router_module, "MAX_FILE_SIZE", 1024)
    _seed(client, data=b"original")

    exact = b"y" * 1024
    res = client.post("/wopi/files/doc1/contents", content=exact)

    assert res.status_code == 200
    assert res.json()["size"] == 1024
    assert client.test_store.get_content("doc1") == exact  # type: ignore[attr-defined]


def test_put_file_accepts_under_max_size(client, monkeypatch):
    """PutFile accepts a body just under MAX_FILE_SIZE (typical case)."""
    monkeypatch.setattr(wopi_router_module, "MAX_FILE_SIZE", 1024)
    _seed(client, data=b"original")

    under = b"z" * 1023
    res = client.post("/wopi/files/doc1/contents", content=under)

    assert res.status_code == 200
    assert res.json()["size"] == len(under)
    assert client.test_store.get_content("doc1") == under  # type: ignore[attr-defined]


def test_put_file_accepts_empty_body(client, monkeypatch):
    """PutFile accepts a zero-byte body (a valid document is not oversized)."""
    monkeypatch.setattr(wopi_router_module, "MAX_FILE_SIZE", 1024)
    _seed(client, data=b"original")

    res = client.post("/wopi/files/doc1/contents", content=b"")

    assert res.status_code == 200
    assert res.json()["size"] == 0
    assert client.test_store.get_content("doc1") == b""  # type: ignore[attr-defined]


# -----------------------------------------------------------------------------
# 2. Lock contention (foreign-lock ops)
# -----------------------------------------------------------------------------


def test_put_file_rejects_foreign_lock(client):
    """PutFile returns 409 for a mismatched lock token and keeps old content."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"original")
    store.set_lock("doc1", "L1", "alice")

    res = client.post(
        "/wopi/files/doc1/contents",
        content=b"modified",
        headers={"X-WOPI-Lock": "WRONG"},
    )

    assert res.status_code == 409
    assert "Lock mismatch" in res.json()["error"]
    assert store.get_content("doc1") == b"original"


def test_put_file_accepts_matching_lock(client):
    """PutFile succeeds when the X-WOPI-Lock token matches the current lock."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"original")
    store.set_lock("doc1", "L1", "alice")

    res = client.post(
        "/wopi/files/doc1/contents",
        content=b"modified",
        headers={"X-WOPI-Lock": "L1"},
    )

    assert res.status_code == 200
    assert store.get_content("doc1") == b"modified"


def test_put_file_rejects_missing_lock_when_file_locked(client):
    """PutFile without a lock header on a locked file is a lock mismatch (409)."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"original")
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/contents", content=b"modified")

    assert res.status_code == 409
    assert "Lock mismatch" in res.json()["error"]
    assert store.get_content("doc1") == b"original"


def test_put_file_accepts_unlocked_file_without_lock_header(client):
    """PutFile without a lock header succeeds on an unlocked file."""
    _seed(client, data=b"original")

    res = client.post("/wopi/files/doc1/contents", content=b"modified")

    assert res.status_code == 200
    assert client.test_store.get_content("doc1") == b"modified"  # type: ignore[attr-defined]


def test_lock_conflict_echoes_winner_token(client):
    """Lock with a foreign token returns 409 and echoes the winner's token in
    X-WOPI-Lock so clients can adopt or back off (first-writer-wins)."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/lock", headers={"X-WOPI-Lock": "L2"})

    assert res.status_code == 409
    assert "Lock mismatch" in res.json()["error"]
    assert res.headers.get("X-WOPI-Lock") == "L1"
    assert store.get_lock("doc1") == "L1"


def test_lock_same_token_is_refresh(client):
    """Lock with the current token is a refresh: 200 and the lock is kept."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/lock", headers={"X-WOPI-Lock": "L1"})

    assert res.status_code == 200
    assert res.headers.get("X-WOPI-Lock") == "L1"
    assert store.get_lock("doc1") == "L1"


def test_lock_empty_token_rejected(client):
    """Lock with an empty token is rejected (400): WOPI tokens must be non-empty."""
    _seed(client)

    res = client.post("/wopi/files/doc1/lock", headers={"X-WOPI-Lock": ""})

    assert res.status_code == 400
    assert "Lock token must be non-empty" in res.json()["error"]
    assert client.test_store.get_lock("doc1") == ""  # type: ignore[attr-defined]


def test_unlock_wrong_token_rejected(client):
    """Unlock with a foreign token returns 409 and keeps the lock."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/unlock", headers={"X-WOPI-Lock": "WRONG"})

    assert res.status_code == 409
    assert "Lock mismatch" in res.json()["error"]
    assert store.get_lock("doc1") == "L1"


def test_unlock_matching_token_releases(client):
    """Unlock with the correct token releases the lock (lock becomes empty)."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/unlock", headers={"X-WOPI-Lock": "L1"})

    assert res.status_code == 200
    assert store.get_lock("doc1") == ""


def test_refresh_lock_rejects_foreign_token(client):
    """RefreshLock with a foreign token returns 409 and keeps the lock."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/refreshlock", headers={"X-WOPI-Lock": "WRONG"})

    assert res.status_code == 409
    assert "Lock mismatch" in res.json()["error"]
    assert store.get_lock("doc1") == "L1"


def test_refresh_lock_matching_token_extends(client):
    """RefreshLock with the matching token extends the lease (200, lock kept)."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/refreshlock", headers={"X-WOPI-Lock": "L1"})

    assert res.status_code == 200
    assert res.headers.get("X-WOPI-Lock") == "L1"
    assert store.get_lock("doc1") == "L1"


def test_refresh_lock_acquires_on_unlocked_file(client):
    """RefreshLock on an unlocked file acquires the lock (server contract)."""
    _seed(client)

    res = client.post("/wopi/files/doc1/refreshlock", headers={"X-WOPI-Lock": "L1"})

    assert res.status_code == 200
    assert res.headers.get("X-WOPI-Lock") == "L1"
    assert client.test_store.get_lock("doc1") == "L1"  # type: ignore[attr-defined]


def test_put_file_with_lock_header_on_unlocked_file(client):
    """PutFile with a lock header on an unlocked file writes the content and
    echoes an empty lock, but does NOT acquire the lock.

    NOTE: existing behaviour — the router only *checks* the current lock on
    PutFile (``current_lock and lock != current_lock``) and never calls
    ``set_lock``, so presenting a token on an unlocked file does not create
    a lock. A separate Lock call is required to lock the file.
    """
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)

    res = client.post(
        "/wopi/files/doc1/contents",
        content=b"new content",
        headers={"X-WOPI-Lock": "L1"},
    )

    assert res.status_code == 200
    assert store.get_content("doc1") == b"new content"
    assert store.get_lock("doc1") == ""


# -----------------------------------------------------------------------------
# 3. GetLock endpoint
# -----------------------------------------------------------------------------


def test_get_lock_returns_current_token_header(client):
    """GetLock echoes the current lock token in X-WOPI-Lock and an empty body."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/getlock")

    assert res.status_code == 200
    assert res.headers.get("X-WOPI-Lock") == "L1"
    assert res.json() == {}


def test_get_lock_returns_space_when_unlocked(client):
    """GetLock echoes a single space in X-WOPI-Lock when the file is unlocked."""
    _seed(client)

    res = client.post("/wopi/files/doc1/getlock")

    assert res.status_code == 200
    assert res.headers.get("X-WOPI-Lock") == " "
    assert res.json() == {}


def test_get_lock_returns_empty_json_body(client):
    """GetLock body is an empty JSON object; the token travels in the header."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/getlock")

    assert res.status_code == 200
    assert res.json() == {}


def test_get_lock_unknown_file_404(client):
    """GetLock for an unknown file returns 404 with a descriptive error."""
    res = client.post("/wopi/files/nonexistent/getlock")

    assert res.status_code == 404
    assert "File not found" in res.json()["error"]
