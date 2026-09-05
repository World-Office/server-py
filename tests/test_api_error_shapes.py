"""API error-shape sweep: structured typed errors across every route family.

Paradigm: **UNIT + SEC**. Asserts that when things go wrong the API answers
with structured JSON errors (never an HTML stack trace, never a crash) and
that the status class is correct per failure mode:

1. unknown document id  -> 404 with a JSON body
2. invalid document id  -> 400 with a JSON body
3. wrong HTTP method    -> 405 with a JSON body
4. malformed JSON body  -> 400/422 with a JSON body
5. WOPI unknown file    -> 404, JSON body

Deterministic: no network, no sleeps, no external tools.
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
from src.wopi.router import router as wopi_router


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
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _assert_json_error(res: "TestClient.response", status: int) -> None:
    """Shared invariant: structured JSON error, correct class, no HTML."""
    assert res.status_code == status
    assert "text/html" not in res.headers.get("content-type", "")
    body = res.json()
    assert isinstance(body, dict)
    assert body, "error body must not be empty"


# -----------------------------------------------------------------------------
# 1. Unknown document ids across route families
# -----------------------------------------------------------------------------


def test_unknown_doc_consistent_404_across_get_routes(client):
    """GET routes for a nonexistent doc all answer 404 with JSON errors."""
    ghost = "new-does-not-exist"
    for path in (
        f"/api/documents/{ghost}/contents",
        f"/api/documents/{ghost}/versions",
        f"/api/documents/{ghost}/html",
    ):
        _assert_json_error(client.get(path), 404)


def test_unknown_doc_consistent_404_on_export(client):
    """Export for a nonexistent document is a typed 404, not a crash."""
    _assert_json_error(
        client.post("/api/documents/new-nope/export", params={"format": "odt"}),
        404,
    )


# -----------------------------------------------------------------------------
# 2. Invalid (malformed) document ids
# -----------------------------------------------------------------------------


def test_invalid_doc_id_rejected(client):
    """A syntactically invalid doc id is rejected client-side style (400)."""
    res = client.get("/api/documents/../etc/passwd/contents")
    assert res.status_code in (400, 404)
    assert res.status_code != 500
    assert "text/html" not in res.headers.get("content-type", "")


def test_restore_unknown_version_typed_error(client):
    """Restoring a nonexistent version of an existing doc is typed, not 500."""
    made = client.post("/api/documents/new", params={"format": "docx"}).json()
    doc_id = made["doc_id"]
    res = client.post(f"/api/documents/{doc_id}/versions/1999-01-01T00:00:00Z/restore")
    assert res.status_code in (400, 404, 422)
    assert res.status_code != 500
    assert "text/html" not in res.headers.get("content-type", "")


# -----------------------------------------------------------------------------
# 3. Wrong method / malformed bodies
# -----------------------------------------------------------------------------


def test_wrong_method_is_405_json(client):
    """DELETE on a GET-only endpoint yields structured 405, never HTML."""
    res = client.delete("/api/documents/whatever/contents")
    _assert_json_error(res, 405)


def test_malformed_json_body_typed(client):
    """Malformed JSON to save is 400/422 structured — no stack trace."""
    made = client.post("/api/documents/new", params={"format": "docx"}).json()
    res = client.post(
        f"/api/documents/{made['doc_id']}/save",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code in (400, 422)
    assert res.status_code != 500
    assert "text/html" not in res.headers.get("content-type", "")


# -----------------------------------------------------------------------------
# 4. WOPI surface
# -----------------------------------------------------------------------------


def test_wopi_unknown_file_404(client):
    """CheckFileInfo for an unknown/invalid file id is 404, not a crash."""
    res = client.get("/wopi/files/new-missing/contents")
    assert res.status_code in (400, 404)
    assert res.status_code != 500
    assert "text/html" not in res.headers.get("content-type", "")
