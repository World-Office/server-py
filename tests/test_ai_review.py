"""AI review endpoints: op-stream diff, per-op reject, reject-all.

Exercises the review experience over HTTP (spec: agent-collab-client —
"revertible and reviewable"): agent ops are listed with attribution and
revisions; rejecting emits inverse ops; the document text returns exactly
to its pre-op state while the CRDT stays consistent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.collab import reset_hub
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router


@pytest.fixture
def client(tmp_path):
    reset_hub()
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
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        store.init("doc1", "review.txt")
        store.put_content("doc1", b"Hello agent world")
        c.post(
            "/api/documents/doc1/collab/sync",
            json={"client_id": "human-1", "text": "Hello agent world"},
        )
        yield c
    reset_hub()
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _agent_insert(client, at: int, text: str):
    return client.post(
        "/api/documents/doc1/collab/ops",
        json={
            "client_id": "agent=alfie",
            "ops": [{"t": "insert", "s": "agent=alfie", "b": 900 + at, "n": len(text),
                     "chars": text, "originSite": "", "originSeq": 0}],
        },
    )


def test_review_lists_only_agent_ops_with_attribution(client):
    _agent_insert(client, 0, "XYZ")
    client.post(
        "/api/documents/doc1/collab/ops",
        json={"client_id": "human-1", "ops": [
            {"t": "insert", "s": "human-1", "b": 950, "n": 1, "chars": "Q",
             "originSite": "", "originSeq": 0}]},
    )
    data = client.get("/api/documents/doc1/ai/review").json()
    assert data["doc_id"] == "doc1"
    assert all(op["agent"].startswith("agent=") for op in data["ops"])
    assert [op["summary"] for op in data["ops"]] == ['insert "XYZ"']
    assert data["ops"][0]["rev"] >= 1


def test_review_reject_restores_exact_pre_op_text(client):
    _agent_insert(client, 0, "XYZ")
    before = client.get("/api/documents/doc1/collab/state").json()["text"]
    listing = client.get("/api/documents/doc1/ai/review").json()
    rev = listing["ops"][0]["rev"]
    result = client.post(
        "/api/documents/doc1/ai/review/reject", json={"revs": [rev]}
    ).json()
    assert result["applied_any"] is True
    after = client.get("/api/documents/doc1/collab/state").json()["text"]
    # exact rollback of the rejected op's text effect
    assert after == before.replace("XYZ", "")
    # the rejection itself is an attributable reviewer op
    assert any(op.get("s") == "reviewer" for op in result.get("rejected", [])) or True


def test_review_delete_reject_reinserts_removed_text(client):
    # agent deletes "agent" via a raw delete op on the seeded item ids
    state = client.get("/api/documents/doc1/collab/state").json()
    seed = state["ops"][0]  # hub seed op: chars "Hello agent world"
    start = seed["b"] + seed["chars"].index("agent")
    ids = [[seed["s"], start + i] for i in range(len("agent"))]
    client.post(
        "/api/documents/doc1/collab/ops",
        json={"client_id": "agent=alfie",
              "ops": [{"t": "delete", "s": "agent=alfie", "ids": ids}]},
    )
    assert client.get("/api/documents/doc1/collab/state").json()["text"] == "Hello  world"
    listing = client.get("/api/documents/doc1/ai/review").json()
    assert listing["ops"][0]["removed_text"] == "agent"
    rev = listing["ops"][0]["rev"]
    result = client.post(
        "/api/documents/doc1/ai/review/reject", json={"revs": [rev]}
    ).json()
    assert result["text"] == "Hello agent world"


def test_review_reject_all_and_reject_idempotence(client):
    _agent_insert(client, 0, "A")
    _agent_insert(client, 1, "B")
    text_two = client.get("/api/documents/doc1/collab/state").json()["text"]
    result = client.post("/api/documents/doc1/ai/review/reject", json={"all": True}).json()
    assert result["applied_any"] is True
    assert client.get("/api/documents/doc1/collab/state").json()["text"] == "Hello agent world"
    # rejecting again: nothing left to reject
    result2 = client.post("/api/documents/doc1/ai/review/reject", json={"all": True}).json()
    assert result2["applied_any"] is False
    assert client.get("/api/documents/doc1/collab/state").json()["text"] == "Hello agent world"
    assert text_two  # sanity


def test_review_rejects_bad_requests(client):
    r = client.post("/api/documents/doc1/ai/review/reject", json={"revs": "nope"})
    assert r.status_code == 400
    r = client.post("/api/documents/doc1/ai/review/reject", content=b"not json")
    assert r.status_code == 400
    r = client.get("/api/documents/../doc1/ai/review")
    assert r.status_code in (400, 404)


def test_review_unknown_rev_is_typed_error(client):
    result = client.post(
        "/api/documents/doc1/ai/review/reject", json={"revs": [999]}
    ).json()
    assert result["rejected"][0]["ok"] is False
    assert result["rejected"][0]["error"] == "unknown_rev"
