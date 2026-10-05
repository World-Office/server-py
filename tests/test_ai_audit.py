"""Agent run audit (E20): every agent turn leaves a queryable row.

Rows record WHO ran, WHEN, on which document, with what budget usage and
stop reason; aggregates answer 'what did the agents do' at a glance. The
REST surface (/api/agents/runs, /api/agents/summary) is the operator view.
"""

from __future__ import annotations

import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from src.ai.runner import AgentRunner
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.editor.router import router
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def world(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "audit.docx")
    store.put_content("doc1", _docx_bytes("audit base"))
    yield store
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


@pytest.fixture
def client(world, tmp_path):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.state.store = world
    with TestClient(app) as c:
        yield c


class EchoModel:
    """Scripted model: one apply_ops call, then stop."""

    def __init__(self, ops):
        self.ops = ops
        self.done = False

    def __call__(self, messages):
        if self.done:
            return []
        self.done = True
        return [{"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=auditor", "ops": self.ops,
        }}]


def test_runner_writes_audit_rows(world):
    runner = AgentRunner(EchoModel([{"t": "ins", "at": 0, "text": "hi "}]))
    report = runner.run(ToolContext(store=world, hub=CollabHub()),
                        "doc1", "agent=auditor", "greet the doc", audit=world)
    assert report.ops_applied == 1
    rows = world.list_agent_runs(client_id="agent=auditor")
    assert len(rows) == 1
    row = rows[0]
    assert row["doc_id"] == "doc1" and row["steps"] == 2 and row["ops"] == 1
    assert row["stopped_reason"] == "done" and row["task"] == "greet the doc"
    assert isinstance(row["rev"], int) and row["id"] > 0


def test_audit_rows_filter_and_order(world):
    world.record_agent_run("doc1", "agent=a", task="t1", steps=1, ops=0, rev=1, stopped_reason="done", ts=100)
    world.record_agent_run("doc2", "agent=b", task="t2", steps=2, ops=3, rev=9, stopped_reason="max_ops", ts=200)
    world.record_agent_run("doc1", "agent=a", task="t3", steps=1, ops=1, rev=2, stopped_reason="done", ts=300)
    assert [r["task"] for r in world.list_agent_runs()] == ["t3", "t2", "t1"]  # newest first
    assert [r["task"] for r in world.list_agent_runs(client_id="agent=a")] == ["t3", "t1"]
    assert [r["task"] for r in world.list_agent_runs(doc_id="doc2")] == ["t2"]


def test_summary_aggregates(world):
    world.record_agent_run("doc1", "agent=a", ops=2, ts=100)
    world.record_agent_run("doc1", "agent=a", ops=5, ts=200)
    world.record_agent_run("doc2", "agent=a", ops=1, ts=300)
    world.record_agent_run("doc9", "agent=b", ops=7, ts=400)
    agents = {a["client_id"]: a for a in world.agent_summary()}
    assert agents["agent=a"]["runs"] == 3 and agents["agent=a"]["ops"] == 8
    assert agents["agent=a"]["docs"] == 2
    assert agents["agent=b"]["ops"] == 7 and agents["agent=b"]["last_ts"] == 400
    assert list(agents) == ["agent=b", "agent=a"]  # most recently active first


def test_rest_runs_and_summary(client, world):
    world.record_agent_run("doc1", "agent=rest", task="rest", steps=3, ops=2, rev=5, stopped_reason="done")
    runs = client.get("/api/agents/runs").json()["runs"]
    assert runs[0]["client_id"] == "agent=rest" and runs[0]["ops"] == 2
    filtered = client.get("/api/agents/runs", params={"client_id": "agent=none"}).json()["runs"]
    assert filtered == []
    bad = client.get("/api/agents/runs", params={"limit": 0})
    assert bad.status_code == 400
    summary = client.get("/api/agents/summary").json()["agents"]
    assert summary[0]["client_id"] == "agent=rest" and summary[0]["runs"] == 1


def test_audit_failure_never_breaks_the_run(world):
    class BrokenStore:
        def record_agent_run(self, **kw):
            raise RuntimeError("disk on fire")

        def get(self, doc_id):
            return world.get(doc_id)

        def get_content(self, doc_id):
            return world.get_content(doc_id)

    ctx = ToolContext(store=world, hub=CollabHub())
    runner = AgentRunner(EchoModel([{"t": "ins", "at": 0, "text": "still lands"}]))
    report = runner.run(ctx, "doc1", "agent=tough", "ignore broken audit", audit=BrokenStore())
    assert report.ops_applied == 1  # the edit went through regardless
    assert world.list_agent_runs() == []  # but nothing was recorded


def test_agents_dashboard_renders(client, world):
    """E20S3: a read-only server-rendered page — aggregates on top, recent
    runs underneath. Stoic: a page, not a SPA."""
    world.record_agent_run("doc1", "agent=dash", task="page check", steps=2, ops=3, rev=4, stopped_reason="done")
    resp = client.get("/agents")
    assert resp.status_code == 200
    body = resp.text
    assert "agent=dash" in body and "page check" in body  # recent run shown
    assert "runs" in body.lower()  # aggregates section present
