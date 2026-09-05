"""Export & download endpoints (F-002 Download as ODT, F-003 Download as DOCX,
F-004 Export to PDF).

The export contract: the endpoint returns real, engine-produced artifacts —
never silent fallbacks. PDF export must use WeasyPrint (declared dependency);
the historical no-content stub PDF is a bug detector, not a contract.
"""

from __future__ import annotations

import io
import zipfile
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


def _docx_bytes(text: str = "Export me") -> bytes:
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


@pytest.fixture
def docx_doc(client):
    """A stored DOCX document ready for export."""
    client.test_store.init("doc-1.docx", "doc-1.docx")  # type: ignore[attr-defined]
    client.test_store.put_content("doc-1.docx", _docx_bytes())  # type: ignore[attr-defined]
    return "doc-1.docx"


def test_export_docx_is_a_real_docx(client, docx_doc):
    r = client.post("/api/documents/doc-1.docx/export?format=docx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('filename="doc-1.docx"')
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "word/document.xml" in zf.namelist(), "not a DOCX container"
    assert b"Export me" in zf.read("word/document.xml")


def test_export_odt_is_a_real_odt(client, docx_doc):
    r = client.post("/api/documents/doc-1.docx/export?format=odt")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "content.xml" in zf.namelist(), "not an ODT container"
    assert b"Export me" in zf.read("content.xml")


def test_export_html_preserves_content(client, docx_doc):
    r = client.post("/api/documents/doc-1.docx/export?format=html")
    assert r.status_code == 200
    assert "Export me" in r.text


def test_export_pdf_uses_real_engine(client, docx_doc):
    """PDF export must be engine-produced, never the historical stub.

    The stub was ~140 bytes with zero pages; WeasyPrint output is >1 KB and
    carries /Contents streams.
    """
    r = client.post("/api/documents/doc-1.docx/export?format=pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert len(r.content) > 1500, "suspiciously small PDF — stub engine?"
    # Real WeasyPrint output: compressed content streams + a cross-reference
    # table. The historical stub (~140 B) had neither.
    assert b"/Filter /FlateDecode" in r.content, "no content streams — stub engine?"
    assert b"startxref" in r.content, "no xref table — stub engine?"
    engine = r.headers.get("x-export-engine", "")
    assert engine == "weasyprint", f"expected weasyprint engine, got {engine!r}"


def test_export_pdf_from_odt_document(client, tmp_path):
    """ODT-sourced exports route through the ODT converter."""
    app, store = _make_app(tmp_path)
    store.init("doc-2.odt", "doc-2.odt")
    store.put_content("doc-2.odt", _docx_bytes())  # bytes mismatched on purpose:
    # format detection is name-based; conversion failure must surface as 500,
    # not a stub — here we only assert the route does not 404/400.
    with TestClient(app) as c:
        r = c.post("/api/documents/doc-2.odt/export?format=pdf")
        assert r.status_code in (200, 500)
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def test_export_rejects_unknown_format(client, docx_doc):
    r = client.post("/api/documents/doc-1.docx/export?format=exe")
    assert r.status_code == 400
    assert "unsupported format" in r.json()["error"]


def test_export_rejects_unknown_document(client):
    r = client.post("/api/documents/nope-404.docx/export?format=pdf")
    assert r.status_code == 404


def test_export_rejects_invalid_document_id(client):
    r = client.post("/api/documents/../etc/passwd/export?format=pdf")
    assert r.status_code in (400, 404)


def test_download_original_odt_bytes_unchanged(client, tmp_path):
    """F-002: downloading an ODT document returns the original bytes."""
    from src.editor.odt_converter import html_to_odt

    original = html_to_odt("<p>original odt bytes</p>")
    client.test_store.init("orig.odt", "orig.odt")  # type: ignore[attr-defined]
    client.test_store.put_content("orig.odt", original)  # type: ignore[attr-defined]
    r = client.get("/api/documents/orig.odt/contents")
    assert r.status_code == 200
    assert r.content == original, "download must return the stored bytes verbatim"
    assert r.headers["content-type"].startswith(
        "application/vnd.oasis.opendocument.text"
    )


def test_weasyprint_is_a_declared_dependency():
    """Environment contract: the real PDF engine must be importable.

    Guards against regressions to the silent stub path (e.g. dependency
    dropped from pyproject or system libs missing in the container).
    """
    import weasyprint

    assert weasyprint.__version__
