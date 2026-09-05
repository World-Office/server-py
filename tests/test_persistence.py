"""Persistence, crash-recovery and exactly-once retry semantics.

Two more testing schools:

* **Crash recovery / durability** — the store's truth lives on disk
  (SQLite index + per-document content files). "Crashing" the process
  (dropping the in-memory object) and reopening the same paths must lose
  nothing: content, names, locks and version history all survive. A
  storage file that is *not* a database at all (corrupted, truncated,
  overwritten) must fail with the store's own typed error at construction
  — never a raw sqlite traceback or a silently-empty fresh store that would
  hide data loss.

* **Idempotent retry / exactly-once semantics** — WOPI and collab clients
  retry (networks drop responses). Retrying a PutFile with the same
  content, re-Locking with the same token, or re-sending the same collab op
  batch must be safe: no error, no corruption, and collab revisions must
  advance only once per unique op.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.collab import reset_hub
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, DocumentStoreError, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router


def _make_app(tmp_path) -> tuple[FastAPI, DocumentStore]:
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
    reset_hub()
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(str(tmp_path / "t.db"))
    wipe_dir(str(tmp_path / "content"))
    reset_hub()


# ---------------------------------------------------------------------------
# Crash recovery / durability
# ---------------------------------------------------------------------------


def test_reopen_preserves_content_names_locks_and_versions(tmp_path):
    db = str(tmp_path / "p.db")
    content = str(tmp_path / "content")
    store1 = DocumentStore(db, content)
    store1.init("a", "a.docx")
    store1.put_content("a", b"AAAA")
    store1.init("b", "b.docx")
    store1.put_content("b", b"BBBB")
    store1.set_lock("a", "L-1", user="alice")
    store1.put_version("a", b"snapshot-two")
    versions_before = len(store1.list_versions("a"))
    docs_before = len(store1.list())
    del store1  # "crash": only the in-memory object is lost

    store2 = DocumentStore(db, content)  # reopen the same paths
    assert store2.get("a")["name"] == "a.docx"
    assert store2.get_content("a") == b"AAAA"
    assert store2.get_content("b") == b"BBBB"
    assert store2.get_lock("a") == "L-1"
    assert store2.list_versions("a"), "version history must survive reopen"
    assert len(store2.list_versions("a")) >= versions_before
    assert len(store2.list()) == docs_before


def test_lock_taken_after_reopen_still_honoured(tmp_path):
    db = str(tmp_path / "p.db")
    content = str(tmp_path / "content")
    s1 = DocumentStore(db, content)
    s1.init("a", "a.docx")
    s1.put_content("a", b"x")
    s1.set_lock("a", "keep-me")
    del s1
    s2 = DocumentStore(db, content)
    assert s2.get_lock("a") == "keep-me"
    s2.release_lock("a")
    assert s2.get_lock("a") == ""


def test_absent_db_file_initialises_fresh(tmp_path):
    db = str(tmp_path / "brand-new.db")
    content = str(tmp_path / "content")
    store = DocumentStore(db, content)
    store.init("fresh", "fresh.docx")
    store.put_content("fresh", b"y")
    assert store.get_content("fresh") == b"y"
    assert store.get("fresh") is not None


def test_garbage_db_fails_with_typed_store_error(tmp_path):
    """A storage file that is not a database must raise the store's OWN typed
    error at construction — never a raw sqlite traceback, and never a silent
    fresh store that would hide data loss."""
    db = tmp_path / "garbage.db"
    db.write_bytes(b"this is not a sqlite database at all" * 16)
    with pytest.raises(DocumentStoreError):
        DocumentStore(str(db), str(tmp_path / "content"))


def test_truncated_db_fails_with_typed_store_error(tmp_path):
    """A truncated/corrupted sqlite file (e.g. partial crash write) must not
    yield a silently-empty store either."""
    db = tmp_path / "trunc.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\xde\xad" * 200)
    with pytest.raises(DocumentStoreError):
        DocumentStore(str(db), str(tmp_path / "content"))


# ---------------------------------------------------------------------------
# Path-traversal defence on the editor API (shared boundary predicate)
# ---------------------------------------------------------------------------

# Encoded '/' forms are rejected by ROUTING itself (httpx/ASGI resolve the
# dot-segments -> 404, store untouched); '%5C'/'%2E' forms reach the handler
# and must be bounced by the id guard (400). Established in test_wopi.py.
_HANDLER_REACHING = [
    "..%5C..%5Csecret",
    "..%5Csecret",
    "%2E%2E%5Csecret",
    "%2E%2E",
    "%2E",
    "..%00x",
]
_ROUTING_REJECTED = [
    "..%2Fsecret",
    "%2E%2E%2Fsecret",
    "..%2F..%2Fsecret",
]


def _outside_content(client, content_dir: str) -> list[Path]:
    """Files that landed OUTSIDE the content dir during an attack (the
    sqlite db itself is legitimate and excluded)."""
    root = Path(content_dir).parent
    db = root / "t.db"
    out = []
    for p in root.rglob("*"):
        if p == db or p == db.with_suffix(".db-journal"):
            continue
        if not str(p).startswith(str(Path(content_dir))) and p.is_file():
            out.append(p)
    return out


def test_invalid_doc_id_predicate_is_shareable_and_total():
    """The boundary predicate (in wopi.protocol) is a pure total function:
    every traversal shape is rejected, every safe opaque id accepted."""
    from src.wopi.protocol import invalid_doc_id

    for bad in [
        "", ".", "..", "../secret", "..\\secret", "a/b", "a\\b",
        "x\x00y", "a/../b", "..%2Fsecret", "..%252Fsecret",
    ]:
        assert invalid_doc_id(bad), f"{bad!r} must be rejected"
    for good in ["doc1", "new-123", "42", "ünïcödé", "alpha-beta_gamma", "a.b"]:
        assert not invalid_doc_id(good), f"{good!r} must be accepted"


def test_save_with_traversal_doc_id_writes_nowhere(client, tmp_path):
    """A traversal id on the save endpoint must neither 500 nor create a
    file outside the store's content directory (the doc id becomes the
    content filename via ``f'{doc_id}.bin'``)."""
    content_dir = str(tmp_path / "content")
    for enc in _HANDLER_REACHING + _ROUTING_REJECTED:
        res = client.post(
            f"/api/documents/{enc}/save",
            json={"html": "<p>owned</p>"},
        )
        assert res.status_code in (400, 404), f"{enc} -> {res.status_code}"
    assert _outside_content(client, content_dir) == []


def test_upload_with_traversal_filename_writes_nowhere(client, tmp_path):
    """The upload endpoint derives the doc id from the client-supplied
    filename — a traversal filename must be rejected, not written out of the
    content directory."""
    content_dir = str(tmp_path / "content")
    for fname in ["../../owned.docx", "..\\..\\owned.docx", "..%2F..%2Fowned.docx"]:
        res = client.post(
            "/api/upload",
            files={"file": (fname, b"DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert res.status_code == 400, f"{fname} -> {res.status_code}"
    assert _outside_content(client, content_dir) == []
    # non-traversal uploads still work
    ok = client.post(
        "/api/upload",
        files={"file": ("good.docx", b"DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert ok.status_code == 200
    assert (Path(content_dir) / "good.docx.bin").read_bytes() == b"DOCX"


def test_editor_read_endpoints_reject_traversal_ids(client):
    """Handler-reached traversal ids must be bounced with 400 (never 404
    leakage, never 500); routing-rejected forms never reach the store."""
    for method, tmpl in [
        ("GET", "/api/documents/{e}/html"),
        ("GET", "/api/documents/{e}/contents"),
        ("GET", "/api/documents/{e}"),
        ("PUT", "/api/documents/{e}/contents"),
    ]:
        for enc in _HANDLER_REACHING:
            res = client.request(method, tmpl.format(e=enc))
            assert res.status_code == 400, f"{method} {enc} -> {res.status_code}"
        for enc in _ROUTING_REJECTED:
            res = client.request(method, tmpl.format(e=enc))
            assert res.status_code == 404, f"{method} {enc} -> {res.status_code}"


# ---------------------------------------------------------------------------
# Idempotent retry / exactly-once semantics
# ---------------------------------------------------------------------------


def test_putfile_retry_is_stable(client):
    store = client.test_store  # type: ignore[attr-defined]
    store.init("r", "r.docx")
    store.put_content("r", b"initial")
    store.set_lock("r", "L")
    for _ in range(3):  # client retries the same save
        res = client.post(
            "/wopi/files/r/contents",
            content=b"content X",
            headers={"X-WOPI-Lock": "L"},
        )
        assert res.status_code == 200
    assert store.get_content("r") == b"content X"


def test_collab_op_batch_is_exactly_once_under_retry(client):
    reset_hub()
    batch = json.dumps(
        {
            "client_id": "retry-client",
            "ops": [
                {"t": "insert", "s": "s1", "b": 1, "n": 4, "chars": "once",
                 "originSite": "", "originSeq": 0},
            ],
        }
    )
    first = client.post("/api/documents/r1/collab/ops", content=batch).json()
    assert len(first["applied"]) == 1
    retry = client.post("/api/documents/r1/collab/ops", content=batch).json()
    assert retry["applied"] == []  # deduplicated
    assert retry["rev"] == first["rev"]  # revision advances exactly once
    state = client.get("/api/documents/r1/collab/state").json()
    assert state["text"] == "once"  # the character appears exactly once


def test_lock_retry_same_token_stays_single_holder(client):
    store = client.test_store  # type: ignore[attr-defined]
    store.init("l", "l.docx")
    for _ in range(3):
        res = client.post("/wopi/files/l/lock", headers={"X-WOPI-Lock": "same"})
        assert res.status_code == 200
    assert store.get_lock("l") == "same"
    res = client.post("/wopi/files/l/getlock")
    assert res.headers.get("X-WOPI-Lock") == "same"
