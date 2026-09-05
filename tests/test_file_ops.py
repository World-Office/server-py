"""Tests for file operations: export (ODT/HTML) and the new-document route."""

from __future__ import annotations

import io
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore


def _make_client(tmp_path: Path) -> TestClient:
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    cfg = Config(database=str(tmp_path / "t.db"), content_dir=str(tmp_path / "content"), jwt_secret="test-secret")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.sessions = SessionRegistry()
        app.state.config = cfg
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(editor_router)
    return TestClient(app)


def _seed_docx(client: TestClient, text: str = "export me") -> str:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    # Use the new-document route to get a fresh doc_id, then overwrite the
    # blank content with the seeded DOCX bytes.
    n = client.post("/api/documents/new?format=docx")
    assert n.status_code == 200, n.text
    doc_id = n.json()["doc_id"]
    put = client.get(f"/editor/{doc_id}")  # ensure a session exists
    assert put.status_code in (200, 307), put.status_code
    put = client.post(
        f"/api/documents/{doc_id}/contents",
        content=data,
        headers={"X-WOPI-Override": "PUT"},
    )
    assert put.status_code == 200, put.text
    return doc_id


def test_export_odt_is_valid_odf(tmp_path):
    """POST /api/documents/{id}/export?format=odt yields openable ODT bytes."""
    with _make_client(tmp_path) as client:
        doc_id = _seed_docx(client)
        resp = client.post(f"/api/documents/{doc_id}/export?format=odt")
        assert resp.status_code == 200, resp.text
        assert "vnd.oasis.opendocument.text" in resp.headers["content-type"]
        data = resp.content
        assert data[:2] == b"PK", "ODT must be a zip archive"
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            mimetype = zf.read("mimetype")
            assert mimetype == b"application/vnd.oasis.opendocument.text"
            assert "content.xml" in zf.namelist()
            content = zf.read("content.xml").decode("utf-8")
        assert "export me" in content


def test_export_html_contains_document_text(tmp_path):
    with _make_client(tmp_path) as client:
        doc_id = _seed_docx(client, "html export text")
        resp = client.post(f"/api/documents/{doc_id}/export?format=html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "html export text" in resp.text
        assert "<script" not in resp.text  # sanitized


def test_new_document_returns_openable_blank(tmp_path):
    """POST /api/documents/new returns an editor URL for a stored doc."""
    with _make_client(tmp_path) as client:
        resp = client.post("/api/documents/new?format=docx")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["url"].startswith("/editor/")
        doc_id = body["doc_id"]
        # The blank document is fetchable as editable HTML.
        html = client.get(f"/api/documents/{doc_id}/html")
        assert html.status_code == 200
