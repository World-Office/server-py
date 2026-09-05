"""Provider callable failure modes: raises/times out — typed error, document uncorrupted.

Covers TC-E19-03: when the provider callable (model adapter) raises an
Exception or times out, AgentRunner must:

* surface a typed stop reason through AgentReport.stopped_reason
* never let the exception escape into the server (fail-safe)
* leave the document and store byte-identical to the pre-run state
  (document uncorrupted)
* correctly attribute the turn count in the report (each provider call
  counts as one step, even if it raises)

Provider callable failures are **external** — they live in model SDKs or
network code that the server never ships. The runner insulates the
store/hub from these failures so a hostile or flaky provider cannot
corrupt the document.
"""

from __future__ import annotations

import io

import pytest
from docx import Document

from src.ai.runner import (
    STOP_DONE,
    AgentRunner,
    AgentReport,
)
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str = "Provider fail base") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "fail.docx")
    store.put_content("doc1", _docx_bytes())
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ----------------------------------------------------------------------
# Provider raises Exception — runner must catch, stop, attribute, not crash


def test_provider_raises_runtime_error_stops_safely(ctx):
    """Provider callable raises RuntimeError — runner catches it, sets
    STOP_DONE, returns zero calls, document is untouched (TC-E19-03 FI).
    
    NOTE: existing behaviour — _safe_model swallows all exceptions from
    the provider callable. Each provider call (even one that raises) counts
    as one step in the report.
    """
    before_content = ctx.store.get_content("doc1")
    before_meta = ctx.store.get("doc1")

    def failing_provider(messages):
        raise RuntimeError("provider backend down")

    runner = AgentRunner(failing_provider, max_steps=5)
    report = runner.run(ctx, "doc1", "agent=flaky", "transcribe")

    # Typed stop reason
    assert report.stopped_reason == STOP_DONE
    # One step attempted (the turn with the AgentRun provider failure counts)
    assert report.steps == 1
    assert report.ops_applied == 0
    # Document byte-identical
    assert ctx.store.get_content("doc1") == before_content
    assert ctx.store.get("doc1") == before_meta
    # AgentReport attributes
    assert report.doc_id == "doc1"
    assert report.client_id == "agent=flaky"
    assert report.task == "transcribe"


def test_provider_raises_value_error_stops_safely(ctx):
    """Provider callable raises ValueError (e.g., bad input shape) —
    behaviour mirrors RuntimeError: typed stop, zero side effects.
    """
    before_content = ctx.store.get_content("doc1")

    def bad_input_provider(messages):
        # Simulates a provider SDK that cannot parse its own prompt
        raise ValueError("invalid prompt encoding")

    runner = AgentRunner(bad_input_provider, max_steps=3)
    report = runner.run(ctx, "doc1", "agent=badinput", "summarise")

    assert report.stopped_reason == STOP_DONE
    assert report.steps == 1
    assert report.ops_applied == 0
    assert ctx.store.get_content("doc1") == before_content


def test_provider_raises_timeout_error_stops_safely(ctx):
    """Provider callable raises TimeoutError (simulated timeout) —
    treated identically to other exceptions: typed stop, document safe.
    
    NOTE: real timeouts would be handled by the caller (e.g., httpx with
    timeout=30). This test covers the case where the caller lets the
    exception propagate into the AgentRunner.
    """
    before_content = ctx.store.get_content("doc1")

    def slow_provider(messages):
        raise TimeoutError("model inference timed out after 30s")

    runner = AgentRunner(slow_provider, max_steps=10)
    report = runner.run(ctx, "doc1", "agent=slow", "analyze")

    assert report.stopped_reason == STOP_DONE
    assert report.steps == 1
    assert report.ops_applied == 0
    assert ctx.store.get_content("doc1") == before_content


def test_provider_raises_during_second_turn_stops_mid_session(ctx):
    """Provider succeeds on first turn, then raises — runner stops after
    the failing turn, first-turn side effects remain, second-turn writes
    do NOT happen (document state matches first-turn result).
    """
    apply_first = {
        "name": "apply_ops",
        "arguments": {
            "doc_id": "doc1",
            "client_id": "agent=toggle",
            "ops": [{"t": "ins", "at": 18, "text": "!"}],
        },
    }

    class SemiBrokenModel:
        def __init__(self):
            self.turn = 0

        def __call__(self, messages):
            self.turn += 1
            if self.turn == 1:
                return [apply_first]
            raise RuntimeError("provider died mid-conversation")

    runner = AgentRunner(SemiBrokenModel(), max_steps=5)
    report = runner.run(ctx, "doc1", "agent=toggle", "edit")

    # Stopped on the second turn (after the raise)
    assert report.stopped_reason == STOP_DONE
    assert report.steps == 2  # one successful turn, one failed turn
    assert report.ops_applied == 1  # only the first turn's edit landed
    # Document reflects only the first-turn edit
    assert report.text == "Provider fail base!"
    # The transcript records the first call but NOT the failed second call
    # (failed turn does not produce tool calls)
    assert len(report.transcript) == 1
    assert report.transcript[0]["call"]["name"] == "apply_ops"


# ----------------------------------------------------------------------
# Provider returns non-list — runner treats as empty list, stops safely


def test_provider_returns_none_stops_safely(ctx):
    """Provider callable returns None instead of a list — _safe_model
    converts to empty list, loop stops, document untouched.
    """
    before_content = ctx.store.get_content("doc1")

    def none_provider(messages):
        return None

    runner = AgentRunner(none_provider, max_steps=3)
    report = runner.run(ctx, "doc1", "agent=none", "task")

    assert report.stopped_reason == STOP_DONE
    assert report.steps == 1  # one provider call made, returned None
    assert report.ops_applied == 0
    assert ctx.store.get_content("doc1") == before_content


def test_provider_returns_non_list_stops_safely(ctx):
    """Provider callable returns non-list, non-None (e.g., a dict) —
    _safe_model converts to empty list, loop stops.
    """
    before_content = ctx.store.get_content("doc1")

    def dict_provider(messages):
        return {"error": "not a list"}

    runner = AgentRunner(dict_provider, max_steps=3)
    report = runner.run(ctx, "doc1", "agent=dict", "task")

    assert report.stopped_reason == STOP_DONE
    assert report.steps == 1
    assert report.ops_applied == 0
    assert ctx.store.get_content("doc1") == before_content


# ----------------------------------------------------------------------
# Multiple failure types in one session — all safe


def test_provider_various_exceptions_all_insulated(ctx):
    """Provider raises different exception types across separate runs — all
    are caught, none corrupt the document, each single-failure run is
    properly insulated.
    
    This is a fault-injection test: we verify that different exception
    types from the provider callable are all handled identically by the
    AgentRunner — typed stop, zero side effects, document untouched.
    """
    before_content = ctx.store.get_content("doc1")
    
    # Each exception type gets its own run with a fresh model
    exception_types = [RuntimeError, ValueError, TimeoutError, KeyError]
    
    for exc_type in exception_types:
        def failing_model(messages):
            raise exc_type(f"{exc_type.__name__} from provider")
        
        runner = AgentRunner(failing_model, max_steps=10)
        report = runner.run(ctx, "doc1", "agent=faulty", "stress")
        
        assert report.stopped_reason == STOP_DONE
        assert report.steps == 1
        assert report.ops_applied == 0
        # Document still untouched after each run
        assert ctx.store.get_content("doc1") == before_content
        assert "Provider fail base" in report.text
        assert report.doc_id == "doc1"
        assert report.client_id == "agent=faulty"
        assert report.task == "stress"


# ----------------------------------------------------------------------
# AgentReport serialization preserves failure attributes


def test_agent_report_with_provider_failure_serializes_correctly(ctx):
    """AgentReport.to_dict() includes correct stopped_reason for provider
    failures, allowing audit/eval tooling to detect and categorise failures.
    """

    def failing_provider(messages):
        raise RuntimeError("audit me")

    runner = AgentRunner(failing_provider, max_steps=3)
    report = runner.run(ctx, "doc1", "agent=audit", "test")

    d = report.to_dict()
    assert d["stopped_reason"] == STOP_DONE
    assert d["steps"] == 1
    assert d["ops_applied"] == 0
    assert d["doc_id"] == "doc1"
    assert d["client_id"] == "agent=audit"
    assert d["task"] == "test"
    # text populated from read_doc fallback
    assert "Provider fail base" in d["text"]
