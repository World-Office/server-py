"""AgentRunner: model-agnostic loop, budgets (runaway protection), attribution."""

from __future__ import annotations

import io

import pytest
from docx import Document

from src.ai.runner import (
    STOP_DONE,
    STOP_MAX_OPS,
    STOP_MAX_STEPS,
    AgentRunner,
    ScriptedModel,
)
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str = "Runner base") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "r.docx")
    store.put_content("doc1", _docx_bytes())
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


APPLY = {
    "name": "apply_ops",
    "arguments": {
        "doc_id": "doc1", "client_id": "agent=runner",
        "ops": [{"t": "ins", "at": 11, "text": "!"}],
    },
}


def test_scripted_agent_applies_edits_and_reports(ctx):
    report = AgentRunner(ScriptedModel(APPLY, APPLY)).run(ctx, "doc1", "agent=runner", "shout")
    # the second call compiles a fresh seq (lamport advanced), so both
    # inserts land — identical *edit intents* are not deduped, only
    # identical wire ops are.
    assert report.stopped_reason == STOP_DONE
    assert report.ops_applied == 2
    assert report.text == "Runner base!!"
    assert report.rev >= 2


def test_agent_ops_are_attributable_in_the_log(ctx):
    AgentRunner(ScriptedModel(APPLY)).run(ctx, "doc1", "agent=runner", "t")
    ops = ctx.hub.ops_since("doc1", 0)
    assert any(op.get("s") == "agent=runner" for op in ops)


def test_max_steps_budget_stops_a_runaway_model(ctx):
    class ChattyModel:
        """Always wants another edit — the loop must stop it."""

        def __init__(self):
            self.n = 0

        def __call__(self, messages):
            self.n += 1
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=loop",
                "ops": [{"t": "ins", "at": 0, "text": "x"}],
            }}]

    model = ChattyModel()
    report = AgentRunner(model, max_steps=5).run(ctx, "doc1", "agent=loop", "spin")
    assert report.stopped_reason == STOP_MAX_STEPS
    assert report.steps == 5
    assert report.ops_applied == 5  # each insert is distinct (fresh seq)
    assert len(report.text) == len("Runner base") + 5


def test_max_ops_budget_stops_before_the_hub_floods(ctx):
    class OneCallManyOps:
        def __call__(self, messages):
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=flood",
                "ops": [{"t": "ins", "at": 0, "text": "y"}] * 10,
            }}]

    # 10 identical edit intents per call each compile a fresh seq, so one
    # call applies all 10; the op budget then stops any further calls
    # (overshoot within a single tool call is possible and bounded by the
    # per-call batch cap in the tool layer).
    report = AgentRunner(OneCallManyOps(), max_steps=50, max_ops=3).run(
        ctx, "doc1", "agent=flood", "flood"
    )
    assert report.stopped_reason == STOP_MAX_OPS
    assert report.ops_applied == 10  # first call landed whole, then the loop stopped


def test_broken_model_stops_the_loop_safely(ctx):
    def broken(messages):
        raise RuntimeError("provider down")

    report = AgentRunner(broken, max_steps=5).run(ctx, "doc1", "agent=x", "t")
    assert report.stopped_reason == STOP_DONE
    assert report.ops_applied == 0
    assert "Runner base" in report.text


def test_runner_rejects_non_callable_model():
    with pytest.raises(TypeError):
        AgentRunner("not-callable")  # type: ignore[arg-type]


def test_transcript_records_calls_and_results(ctx):
    report = AgentRunner(ScriptedModel(APPLY)).run(ctx, "doc1", "agent=runner", "t")
    assert len(report.transcript) == 1
    entry = report.transcript[0]
    assert entry["call"]["name"] == "apply_ops"
    assert entry["result"]["ok"] is True
