"""Document info / metadata endpoint (F-006).

GET /api/documents/{id}/info exposes the docserver-side metadata: name,
format, byte size, timestamps, version. The OpenCloud shell shows file
properties in its own details panel; this endpoint is the docserver's
source of truth for them.
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir


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
    app.include_router(editor_router)
    return app, store


def _docx_bytes(text: str = "Info probe") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def test_info_reports_metadata(client):
    data = _docx_bytes()
    client.test_store.init("doc-1.docx", "doc-1.docx")  # type: ignore[attr-defined]
    client.test_store.put_content("doc-1.docx", data)  # type: ignore[attr-defined]
    r = client.get("/api/documents/doc-1.docx/info")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "doc-1.docx"
    assert body["format"] == "docx"
    assert body["size"] == len(data)
    assert body["created_at"] and body["updated_at"]
    assert body["version"] == str(body["updated_at"])


def test_info_detects_odt_format(client):
    client.test_store.init("doc-2.odt", "doc-2.odt")  # type: ignore[attr-defined]
    client.test_store.put_content("doc-2.odt", b"PK\x03\x04stub")  # type: ignore[attr-defined]
    body = client.get("/api/documents/doc-2.odt/info").json()
    assert body["format"] == "odt"


def test_info_404_on_unknown_document(client):
    assert client.get("/api/documents/ghost.docx/info").status_code == 404


def test_info_rejects_invalid_document_id(client):
    assert client.get("/api/documents/../etc/info").status_code in (400, 404)
