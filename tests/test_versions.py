"""Version history: store snapshots + editor API (T30, version-history).

The docserver keeps byte-level snapshots of every content write so users
can list prior versions and restore one. Host-mode (local store) only;
remote (client-mode) documents return a clear error instead.
"""
from __future__ import annotations

import io
from contextlib import asynccontextmanager

import pytest
from docx import Document as DocxDocument
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.converter import docx_to_html
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
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
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _docx_bytes(text: str) -> bytes:
    doc = DocxDocument()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------
# Store level
# ----------------------------------------------------------------------


def test_store_snapshots_each_put_with_author(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "a.docx")
    store.put_content("doc1", b"v1", author="alice")
    store.put_content("doc1", b"v2", author="bob")
    versions = store.list_versions("doc1")
    assert len(versions) == 2
    # newest first
    assert versions[0]["author"] == "bob"
    assert versions[0]["size"] == 2
    assert versions[1]["author"] == "alice"
    assert store.get_version("doc1", versions[1]["ts"]) == b"v1"


def test_store_prunes_versions_beyond_limit(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "a.docx")
    for i in range(store.MAX_VERSIONS + 10):
        store.put_content("doc1", f"v{i}".encode(), author="u")
    versions = store.list_versions("doc1")
    assert len(versions) <= store.MAX_VERSIONS
    # newest must be retained; oldest must have been dropped
    assert versions[0]["size"] == 3  # "v260" (the last written)


def test_store_restore_rewinds_content_and_is_undoable(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "a.docx")
    store.put_content("doc1", b"version-a", author="u")
    store.put_content("doc1", b"version-b", author="u")
    old = store.list_versions("doc1")
    store.restore_version("doc1", old[-1]["ts"])  # restore version-a
    assert store.get_content("doc1") == b"version-a"
    # the pre-restore state (version-b) is preserved as a new snapshot for undo
    versions = store.list_versions("doc1")
    assert len(versions) > len(old)
    assert versions[0]["size"] == len(b"version-a")  # head = restored content
    assert any(
        v["size"] == len(b"version-b") and v["ts"] != old[0]["ts"]
        for v in versions
    )


def test_store_restore_unknown_version_raises(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "a.docx")
    store.put_content("doc1", b"x", author="u")
    with pytest.raises(Exception):
        store.restore_version("doc1", 1234567890123)


# ----------------------------------------------------------------------
# Editor API
# ----------------------------------------------------------------------


def _seed_versions(client) -> str:
    """Register a doc and write two versions through put_content."""
    store = client.test_store  # type: ignore[attr-defined]
    store.init("hist-doc", "h.docx")
    store.put_content("hist-doc", _docx_bytes("first"), author="alice")
    store.put_content("hist-doc", _docx_bytes("second"), author="bob")
    return "hist-doc"


def test_versions_endpoint_lists_newest_first(client):
    doc_id = _seed_versions(client)
    res = client.get(f"/api/documents/{doc_id}/versions")
    assert res.status_code == 200
    data = res.json()
    assert len(data["versions"]) == 2
    assert data["versions"][0]["author"] == "bob"
    assert data["versions"][1]["author"] == "alice"
    assert all("ts" in v and "size" in v for v in data["versions"])


def test_versions_endpoint_unknown_doc_404(client):
    res = client.get("/api/documents/does-not-exist/versions")
    assert res.status_code == 404


def test_restore_endpoint_rewinds_and_persists(client):
    doc_id = _seed_versions(client)
    store = client.test_store  # type: ignore[attr-defined]
    versions = client.get(f"/api/documents/{doc_id}/versions").json()["versions"]
    old_ts = versions[-1]["ts"]  # first write
    res = client.post(f"/api/documents/{doc_id}/versions/{old_ts}/restore")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    # the document on disk is now the restored bytes
    current = docx_to_html(store.get_content(doc_id)).replace("\n", "")
    assert "second" not in current
    assert "first" in current
