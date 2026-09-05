"""TDD tests for cloud-integration export endpoints (editor-cloud-ui T1-T3).

RED phase: these fail until router.py implements POST /api/documents/{doc_id}/export.
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
    app.include_router(editor_router)
    return app, store


def _docx_bytes(text: str = "Test body") -> bytes:
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


def _seed_doc(client, doc_id="doc1", name="hello.docx", data=None):
    store = client.test_store  # type: ignore[attr-defined]
    store.init(doc_id, name)
    store.put_content(doc_id, data or _docx_bytes())


def test_export_odt(client):
    _seed_doc(client)
    res = client.post("/api/documents/doc1/export?format=odt")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith(
        "application/vnd.oasis.opendocument.text"
    )
    assert len(res.content) > 0


def test_export_html(client):
    _seed_doc(client)
    res = client.post("/api/documents/doc1/export?format=html")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert b"Test body" in res.content


def test_export_docx(client):
    _seed_doc(client)
    res = client.post("/api/documents/doc1/export?format=docx")
    assert res.status_code == 200, res.text
    assert "wordprocessingml" in res.headers["content-type"]


def test_export_pdf(client):
    _seed_doc(client)
    res = client.post("/api/documents/doc1/export?format=pdf")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.content[:4] == b"%PDF"
