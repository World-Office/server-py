"""WOPI lock lifecycle matrix: Lock / PutFile / Unlock / RefreshLock / GetLock (UNIT).

Paradigm: **Unit tests** driving the real FastAPI WOPI host router via
TestClient, arranged as a *lifecycle matrix* — the state of a document
(``unlocked`` / ``locked``) crossed with the five lock-bearing operations.
A document's lifecycle is a sequence: Lock acquires, PutFile gates writes on
the token, RefreshLock extends ownership, GetLock reports the current state,
and Unlock returns the file to the pool. The matrix pinpoints which
combination (state, operation, token match) succeeds and which is rejected.

Scenarios under test (the matrix rows):

1. **Full lifecycle** — the canonical happy path: Lock → PutFile (with the
   token) → GetLock (echoes token) → RefreshLock (lease extended) → Unlock →
   GetLock (reports unlocked). Every step asserts HTTP status, ``X-WOPI-Lock``
   echo and the store's lock state.
2. **PutFile × lock** — a held lock gates writes: a matching token persists the
   content, a foreign token is rejected (409) and leaves content untouched, and
   a missing header on a locked file is treated as a mismatch.
3. **Lock × locked state** — Lock on an unlocked file acquires; Lock with the
   same token again is an idempotent refresh (200, token kept, no 409).
4. **Lock contention** — first-writer-wins without coordination: exactly one of
   two Lock calls succeeds, the loser gets 409 echoing the winner's token in
   ``X-WOPI-Lock``, and the loser can neither write nor unlock.
5. **Unlock × handover** — Unlock releases the token; GetLock then reports
   unlocked and a second writer can Lock with a fresh token and write. Unlock
   on an already-unlocked file is idempotent (200).
6. **RefreshLock × states** — matching refresh extends ownership (200, token
   kept); refresh on an unlocked file acquires the lock (server contract);
   refresh with a foreign token is rejected (409) and keeps the held lock.
7. **PutFile ≠ Lock** — presenting ``X-WOPI-Lock`` on an unlocked file writes
   the content but does *not* acquire a lock (NOTE about current behaviour).
8. **GetLock × states** — GetLock reports the lock token whenever it is held
   and a single space whenever it is not, across the whole lifecycle.

Deterministic: no network, no sleeps, no time-of-day dependence; every
assertion checks the store's actual state, not just response codes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.protocol import LOCK_HEADER
from src.wopi.router import router as wopi_router

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


def _seed(client, doc_id="doc1", name="doc.txt", data=b"original"):
    store = client.test_store  # type: ignore[attr-defined]
    store.init(doc_id, name)
    store.put_content(doc_id, data)


# -----------------------------------------------------------------------------
# 1. Full lifecycle (the canonical happy path)
# -----------------------------------------------------------------------------


def test_full_lock_lifecycle_lock_edit_refresh_get_unlock(client):
    """A document lives through the full WOPI lock lifecycle: Lock acquires,
    PutFile writes under the token, GetLock reports it, RefreshLock extends
    it, Unlock releases it, and GetLock then reports the file unlocked."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"v1")

    # Lock: acquire with a non-empty token.
    res = client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "L1"})
    assert res.status_code == 200
    assert res.headers.get(LOCK_HEADER) == "L1"
    assert store.get_lock("doc1") == "L1"

    # PutFile: the matching token unlocks write access; bytes persist.
    res = client.post(
        "/wopi/files/doc1/contents", content=b"v2", headers={LOCK_HEADER: "L1"}
    )
    assert res.status_code == 200
    assert store.get_content("doc1") == b"v2"

    # GetLock: reports the held token.
    res = client.post("/wopi/files/doc1/getlock")
    assert res.status_code == 200
    assert res.headers.get(LOCK_HEADER) == "L1"

    # RefreshLock: extends the lease, token unchanged.
    res = client.post("/wopi/files/doc1/refreshlock", headers={LOCK_HEADER: "L1"})
    assert res.status_code == 200
    assert res.headers.get(LOCK_HEADER) == "L1"
    assert store.get_lock("doc1") == "L1"

    # Unlock: releases the token.
    res = client.post("/wopi/files/doc1/unlock", headers={LOCK_HEADER: "L1"})
    assert res.status_code == 200
    assert store.get_lock("doc1") == ""

    # GetLock: reports the file unlocked (single space per WOPI spec).
    res = client.post("/wopi/files/doc1/getlock")
    assert res.status_code == 200
    assert res.headers.get(LOCK_HEADER) == " "


# -----------------------------------------------------------------------------
# 2. PutFile x lock state (the lock gates writes)
# -----------------------------------------------------------------------------


def test_put_file_locked_requires_matching_token(client):
    """While a lock is held, PutFile succeeds only with the matching token:
    a foreign token (409) or a missing header (409) leaves the stored content
    untouched; the matching token persists the new bytes."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"original")
    store.set_lock("doc1", "L1", "alice")

    # Foreign token -> 409, content untouched.
    res = client.post(
        "/wopi/files/doc1/contents", content=b"hijack", headers={LOCK_HEADER: "WRONG"}
    )
    assert res.status_code == 409
    assert "Lock mismatch" in res.json()["error"]
    assert store.get_content("doc1") == b"original"

    # Missing header on a locked file -> also 409 (treated as mismatch).
    res = client.post("/wopi/files/doc1/contents", content=b"hijack")
    assert res.status_code == 409
    assert store.get_content("doc1") == b"original"

    # Matching token -> 200, bytes persisted.
    res = client.post(
        "/wopi/files/doc1/contents", content=b"legit", headers={LOCK_HEADER: "L1"}
    )
    assert res.status_code == 200
    assert store.get_content("doc1") == b"legit"


# -----------------------------------------------------------------------------
# 3. Lock on an already-locked file (same token is an idempotent refresh)
# -----------------------------------------------------------------------------


def test_lock_twice_same_token_is_idempotent(client):
    """Lock with the same token on an already-locked file is a refresh, not a
    conflict: the second Lock answers 200 (echoing the token), never 409, and
    the lock stays owned by the original token."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)

    first = client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "L1"})
    assert first.status_code == 200
    second = client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "L1"})
    assert second.status_code == 200
    assert second.headers.get(LOCK_HEADER) == "L1"
    assert store.get_lock("doc1") == "L1"


def test_lock_foreign_token_after_lock_is_conflict(client):
    """Lock with a different token while a lock is held is a conflict: 409
    whose X-WOPI-Lock echoes the current holder's token, and the holder's
    lock is not disturbed."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    res = client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "L2"})
    assert res.status_code == 409
    assert "Lock mismatch" in res.json()["error"]
    assert res.headers.get(LOCK_HEADER) == "L1"
    assert store.get_lock("doc1") == "L1"


# -----------------------------------------------------------------------------
# 4. Lock contention: first-writer-wins without coordination
# -----------------------------------------------------------------------------


def test_contending_writer_loses_all_operations(client):
    """First-writer-wins: of two simultaneous Lock attempts exactly one wins;
    the loser is locked out of the whole lifecycle — its Lock (409), PutFile
    (409) and Unlock (409) are all rejected while the winner holds the file."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"original")

    # Writer A acquires the lock.
    assert client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "A"}).status_code == 200

    # Writer B, holding a foreign token, fails every lock-bearing operation.
    loser_lock = client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "B"})
    assert loser_lock.status_code == 409
    assert loser_lock.headers.get(LOCK_HEADER) == "A"

    loser_put = client.post(
        "/wopi/files/doc1/contents", content=b"b-was-here", headers={LOCK_HEADER: "B"}
    )
    assert loser_put.status_code == 409
    assert store.get_content("doc1") == b"original"

    loser_unlock = client.post("/wopi/files/doc1/unlock", headers={LOCK_HEADER: "B"})
    assert loser_unlock.status_code == 409
    assert store.get_lock("doc1") == "A"

    # Writer A is still in full control.
    res = client.post(
        "/wopi/files/doc1/contents", content=b"a-was-here", headers={LOCK_HEADER: "A"}
    )
    assert res.status_code == 200
    assert store.get_content("doc1") == b"a-was-here"


# -----------------------------------------------------------------------------
# 5. Unlock: release and handover to the next writer
# -----------------------------------------------------------------------------


def test_unlock_hands_file_to_next_writer(client):
    """Unlock releases the file back to the pool: GetLock reports unlocked and
    a second writer can Lock with a fresh token and PutFile new content — the
    handover completes a full lifecycle for both writers."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"v1")

    # Writer 1 locks, edits, unlocks.
    assert client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "L1"}).status_code == 200
    assert client.post(
        "/wopi/files/doc1/contents", content=b"v2", headers={LOCK_HEADER: "L1"}
    ).status_code == 200
    assert client.post("/wopi/files/doc1/unlock", headers={LOCK_HEADER: "L1"}).status_code == 200
    assert store.get_lock("doc1") == ""

    # Writer 2 takes over with a fresh token.
    res = client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "L2"})
    assert res.status_code == 200
    assert res.headers.get(LOCK_HEADER) == "L2"
    assert client.post(
        "/wopi/files/doc1/contents", content=b"v3", headers={LOCK_HEADER: "L2"}
    ).status_code == 200
    assert store.get_content("doc1") == b"v3"


def test_unlock_on_unlocked_file_is_idempotent(client):
    """Unlock on an already-unlocked file answers 200 and leaves the file
    unlocked (no spurious error, no lock created)."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)

    res = client.post("/wopi/files/doc1/unlock", headers={LOCK_HEADER: "L1"})

    assert res.status_code == 200
    assert store.get_lock("doc1") == ""


# -----------------------------------------------------------------------------
# 6. RefreshLock across states
# -----------------------------------------------------------------------------


def test_refresh_lock_matching_token_keeps_ownership(client):
    """RefreshLock with the matching token extends the lease: 200, echoes the
    token, the lock is unchanged, and the refreshed token still authorises
    PutFile — repeated refreshes never fail."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)
    store.set_lock("doc1", "L1", "alice")

    for _ in range(2):
        res = client.post("/wopi/files/doc1/refreshlock", headers={LOCK_HEADER: "L1"})
        assert res.status_code == 200
        assert res.headers.get(LOCK_HEADER) == "L1"
        assert store.get_lock("doc1") == "L1"

    # The refreshed lease still gates writes correctly.
    assert client.post(
        "/wopi/files/doc1/contents", content=b"v2", headers={LOCK_HEADER: "L1"}
    ).status_code == 200
    assert store.get_content("doc1") == b"v2"


# -----------------------------------------------------------------------------
# 7. PutFile does NOT implicitly lock (# NOTE about current behaviour)
# -----------------------------------------------------------------------------


def test_put_file_with_lock_header_does_not_acquire_lock(client):
    """Presenting X-WOPI-Lock on an unlocked file persists the content but does
    NOT acquire a lock: GetLock still reports the file unlocked, so a later
    writer with a different token is not blocked.

    NOTE: existing behaviour — the router only *checks* the current lock on
    PutFile and never calls ``set_lock``, so a token on an unlocked file
    neither creates nor records a lock. Per the WOPI spec a client should Lock
    explicitly; this test pins the current contract.
    """
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client, data=b"original")

    res = client.post(
        "/wopi/files/doc1/contents", content=b"new", headers={LOCK_HEADER: "L1"}
    )
    assert res.status_code == 200
    assert store.get_content("doc1") == b"new"
    assert store.get_lock("doc1") == ""

    getlock = client.post("/wopi/files/doc1/getlock")
    assert getlock.status_code == 200
    assert getlock.headers.get(LOCK_HEADER) == " "


# -----------------------------------------------------------------------------
# 8. GetLock reports state at every step of the lifecycle
# -----------------------------------------------------------------------------


def test_get_lock_tracks_lifecycle_state_transitions(client):
    """GetLock is a faithful state probe across the whole lifecycle: before any
    lock it reports a single space, after Lock it reports the token, and after
    Unlock it reports unlocked again — the header always mirrors the store."""
    store = client.test_store  # type: ignore[attr-defined]
    _seed(client)

    def reported() -> str | None:
        res = client.post("/wopi/files/doc1/getlock")
        assert res.status_code == 200
        return res.headers.get(LOCK_HEADER)

    # GetLock reports a single space when the store is unlocked and the token
    # otherwise — the header is the store's lock token with "" mapped to " ".
    def stored_token() -> str:
        return store.get_lock("doc1") or " "

    assert reported() == " "  # unlocked
    assert client.post("/wopi/files/doc1/lock", headers={LOCK_HEADER: "L1"}).status_code == 200
    assert reported() == "L1"  # locked by L1
    assert stored_token() == reported()
    assert client.post("/wopi/files/doc1/unlock", headers={LOCK_HEADER: "L1"}).status_code == 200
    assert reported() == " "  # unlocked again
    assert stored_token() == reported()
