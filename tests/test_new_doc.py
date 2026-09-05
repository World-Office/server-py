"""TDD tests for new-document endpoint (editor-cloud-ui T4-T5).

RED phase: fails until router.py implements POST /api/documents/new.
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


@pytest.fixture
def client(tmp_path):
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def test_new_document_docx(client):
    res = client.post("/api/documents/new?format=docx")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "doc_id" in body
    assert body["url"].startswith("/editor/")
    # the freshly created doc must be openable in the editor
    res2 = client.get(f"/api/documents/{body['doc_id']}/html")
    assert res2.status_code == 200


def test_new_document_odt(client):
    res = client.post("/api/documents/new?format=odt")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"].endswith(".odt")
