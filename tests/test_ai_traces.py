"""Structured agent traces (E20S1): redacted by default, retention-bounded.

A trace answers "what did the agent do" — never "what did the document
say". Long text payloads are truncated, base64 blobs are dropped, structure
is kept. Traces attach to audit runs and are capped by the store.
"""

from __future__ import annotations

import json

import pytest

from src.ai.audit import REDACT_MAX_CHARS, redact_transcript
from src.ai.tools import ToolContext, tool_read_doc
from src.editor.collab import CollabHub


# ----------------------------------------------------------------------
# redaction

def test_long_strings_are_truncated_in_place():
    transcript = [{"call": {"name": "read_doc", "arguments": {"doc_id": "d"}},
                   "result": {"ok": True, "text": "x" * 5000}}]
    red = redact_transcript(transcript)
    out_text = red[0]["result"]["text"]
    assert len(out_text) < 500
    assert out_text.startswith("xxx") and "chars total]" in out_text
    # structure untouched
    assert red[0]["call"]["name"] == "read_doc"


def test_base64_blobs_are_dropped_but_short_values_kept():
    transcript = [{"result": {"content_base64": "AAAA" * 100, "rev": 7,
                              "note": "short"}}]
    red = redact_transcript(transcript)
    assert red[0]["result"]["content_base64"] == "[dropped]"
    assert red[0]["result"]["rev"] == 7
    assert red[0]["result"]["note"] == "short"


def test_redaction_is_recursive_and_original_untouched():
    transcript = [{"result": {"ops": [{"t": "ins", "text": "y" * 900}]}}]
    red = redact_transcript(transcript)
    assert "chars total]" in red[0]["result"]["ops"][0]["text"]
    assert len(transcript[0]["result"]["ops"][0]["text"]) == 900  # source intact
    assert REDACT_MAX_CHARS == 400


def test_redaction_deterministic():
    t = [{"call": {"name": "get_context"}, "result": {"text": "z" * 1000}}]
    assert json.dumps(redact_transcript(t)) == json.dumps(redact_transcript(t))


# ----------------------------------------------------------------------
# runner + store integration (reuse audit fixtures' shape)

def _docx_bytes(text: str) -> bytes:
    import io

    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def world(tmp_path):
    from src.lib.store import DocumentStore, wipe_db, wipe_dir

    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "trace.docx")
    store.put_content("doc1", _docx_bytes("seed " + "long" * 500))
    yield store
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


class BigReadModel:
    def __init__(self):
        self.done = False

    def __call__(self, messages):
        if self.done:
            return []
        self.done = True
        return [{"name": "read_doc", "arguments": {"doc_id": "doc1", "include_content": True}}]


def test_big_documents_materialize_past_the_old_recursion_ceiling(world):
    """Regression: to_string/_ordered_ids recursed once per character, so
    any document beyond ~950 chars crashed materialization. The iterative
    walk handles multi-KB documents; this fixture is 2 KB."""
    text = tool_read_doc(ToolContext(store=world, hub=CollabHub()), "doc1")["text"]
    assert len(text) >= 2000
    assert text.startswith("seed ")


def test_runner_persists_redacted_trace(world):
    from src.ai.runner import AgentRunner
    from src.ai.tools import ToolContext
    from src.editor.collab import CollabHub

    AgentRunner(BigReadModel()).run(ToolContext(store=world, hub=CollabHub()),
                                    "doc1", "agent=tracer", "read big doc", audit=world)
    run = world.list_agent_runs(client_id="agent=tracer")[0]
    trace = world.get_agent_trace(run["id"])
    assert trace is not None and trace["run_id"] == run["id"]
    payload = json.loads(trace["payload"])
    result = payload[0]["result"]
    # the multi-KB document text is bounded: 400 chars + a total marker
    out_text = result["text"]
    assert len(out_text) <= REDACT_MAX_CHARS + 40
    assert out_text.endswith("chars total]") and "2005" in out_text
    # the base64 blob value is gone (key kept, value replaced)
    assert result["content_base64"] == "[dropped]"
    # structure survives: the call and its result envelope are visible
    assert payload[0]["call"]["name"] == "read_doc"
    assert result["ok"] is True


def test_trace_retention_is_bounded(world):
    for i in range(world.MAX_TRACES + 10):
        world.record_agent_trace(run_id=i, payload="{}")
    ids = [r["id"] for r in world._conn.execute(
        "SELECT id FROM agent_traces ORDER BY id").fetchall()]
    assert len(ids) == world.MAX_TRACES
    assert min(ids) == 11  # the oldest ten were evicted


def test_rest_run_detail_with_trace(world, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.ai.runner import AgentRunner
    from src.ai.tools import ToolContext
    from src.editor.collab import CollabHub
    from src.editor.router import router

    AgentRunner(BigReadModel()).run(ToolContext(store=world, hub=CollabHub()),
                                    "doc1", "agent=rest", "rest trace", audit=world)
    app = FastAPI()
    app.include_router(router)
    app.state.store = world
    with TestClient(app) as c:
        run_id = world.list_agent_runs(client_id="agent=rest")[0]["id"]
        detail = c.get(f"/api/agents/runs/{run_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["client_id"] == "agent=rest"
        assert body["trace"][0]["call"]["name"] == "read_doc"
        assert body["trace"][0]["result"]["content_base64"] == "[dropped]"
        assert body["trace"][0]["result"]["text"].endswith("chars total]")
        missing = c.get("/api/agents/runs/99999")
        assert missing.status_code == 404
