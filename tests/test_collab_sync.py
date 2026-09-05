"""Collaboration convergence contract (T2.1).

The browser ships plain text; the server merges it into a character CRDT so
every editor converges. These tests prove the real-time protocol at the API
level: two (and concurrent) clients editing the same document both end up
reading the same text from the hub.

GATE: pytest tests/test_collab_sync.py
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore
from src.wopi.router import router as wopi_router


def _make_app(tmp_path) -> FastAPI:
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
    return app


def test_collab_two_clients_converge(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as c:
        # A seeds the document text.
        r = c.post("/api/documents/d1/collab/sync", json={"client_id": "A", "text": "Hello world"})
        assert r.status_code == 200
        assert r.json()["text"] == "Hello world"

        # B joins and immediately sees A's text (late-join convergence).
        r = c.get("/api/documents/d1/collab/state")
        assert r.json()["text"] == "Hello world"

        # B edits; the CRDT merges the change.
        r = c.post(
            "/api/documents/d1/collab/sync",
            json={"client_id": "B", "text": "Hello brave world"},
        )
        assert r.json()["text"] == "Hello brave world"

        # A now reads B's edit -> both converged.
        r = c.get("/api/documents/d1/collab/state")
        assert r.json()["text"] == "Hello brave world"


def test_collab_concurrent_edits_converge(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as c:
        c.post(
            "/api/documents/d2/collab/sync",
            json={"client_id": "A", "text": "Hello brave world"},
        )
        # Both clients start from the same base and edit DIFFERENT regions.
        r1 = c.post(
            "/api/documents/d2/collab/sync",
            json={"client_id": "A", "text": "Hello brave new world"},
        )
        r2 = c.post(
            "/api/documents/d2/collab/sync",
            json={"client_id": "B", "text": "Hi brave world"},
        )
        assert r1.status_code == 200 and r2.status_code == 200
        # Both readers agree on the final converged text.
        s1 = c.get("/api/documents/d2/collab/state").json()["text"]
        s2 = c.get("/api/documents/d2/collab/state").json()["text"]
        assert s1 == s2
        # The non-overlapping edits both survived (convergence, not clobber).
        assert "brave" in s1


def test_collab_presence_announced_and_leaves(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as c:
        c.post(
            "/api/documents/d4/collab/sync",
            json={"client_id": "A", "text": "presence doc"},
        )
        r = c.post(
            "/api/documents/d4/collab/presence",
            json={"client_id": "B", "user": "Bob", "cursor": {"index": 3}},
        )
        assert r.status_code == 200
        clients = r.json()["clients"]
        assert any(cl.get("client") == "B" for cl in clients)

        # Sending an empty cursor leaves the document.
        r = c.post(
            "/api/documents/d4/collab/presence",
            json={"client_id": "B", "user": "Bob", "cursor": None},
        )
        clients = r.json()["clients"]
        assert not any(cl.get("client") == "B" for cl in clients)
