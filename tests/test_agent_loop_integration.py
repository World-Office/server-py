"""Full agent loop integration tests — scripted model edits converge (INT+PROP).

Tests the complete agent loop stack: AgentRunner driving tool_apply_ops through
the collaboration hub. Each test focuses on one concept in the loop.

TC-E22-07: full agent loop on real hub+tools — scripted model edits converge.
"""

from __future__ import annotations

import io

from docx import Document
from src.ai.runner import AgentRunner, ScriptedModel
from src.ai.tools import ToolContext, tool_apply_ops
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str = "Base content") -> bytes:
    """Produce a minimal docx whose plain-text baseline is *text*."""
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_context(tmp_path, doc_id: str = "doc1") -> ToolContext:
    """Fresh store+hub pair seeded with a document."""
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init(doc_id, "test.docx")
    store.put_content(doc_id, _docx_bytes("Hello agent"))
    return ToolContext(store=store, hub=CollabHub())


def _wipe(tmp_path):
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ----------------------------------------------------------------------
# Tests for the full agent loop (AgentRunner + ScriptedModel)
# ----------------------------------------------------------------------


def test_full_agent_loop_applies_multiple_ops_and_reports(tmp_path):
    """AgentRunner with a scripted model runs multiple steps and reports.

    The runner calls the model, receives tool calls, applies them via the
    tools, and builds a transcript. Two insert calls should both land.
    """
    ctx = _make_context(tmp_path)
    try:
        # First call inserts "!" at index 11 (end), second inserts "?".
        scripted_model = ScriptedModel(
            {
                "name": "apply_ops",
                "arguments": {
                    "doc_id": "doc1",
                    "client_id": "agent=loop1",
                    "ops": [{"t": "ins", "at": 11, "text": "!"}],
                },
            },
            {
                "name": "apply_ops",
                "arguments": {
                    "doc_id": "doc1",
                    "client_id": "agent=loop1",
                    "ops": [{"t": "ins", "at": 12, "text": "?"}],
                },
            },
        )
        runner = AgentRunner(scripted_model)
        report = runner.run(ctx, "doc1", "agent=loop1", "shout at end")

        # Both inserts land (second index shifts after first)
        assert report.stopped_reason == "done"
        assert report.ops_applied == 2
        assert report.text == "Hello agent!?"
        assert report.rev >= 2
        assert len(report.transcript) == 2
        assert report.transcript[0]["call"]["name"] == "apply_ops"
        assert report.transcript[1]["result"]["ok"] is True
    finally:
        _wipe(tmp_path)


def test_agent_loop_attributable_ops_in_collab_log(tmp_path):
    """Every applied op carries the agent's client_id (attribution)."""
    ctx = _make_context(tmp_path)
    try:
        agent_id = "agent=attribution_test"
        scripted_model = ScriptedModel(
            {
                "name": "apply_ops",
                "arguments": {
                    "doc_id": "doc1",
                    "client_id": agent_id,
                    "ops": [{"t": "ins", "at": 0, "text": "A"}],
                },
            }
        )
        runner = AgentRunner(scripted_model)
        runner.run(ctx, "doc1", agent_id, "prepend A")

        # Pull ops from the hub directly
        ops = ctx.hub.ops_since("doc1", 0)
        assert any(op.get("s") == agent_id for op in ops)
        assert any(op.get("t") == "insert" for op in ops)
    finally:
        _wipe(tmp_path)


def test_agent_loop_respects_max_steps_budget(tmp_path):
    """A model that always wants to edit stops when max_steps is reached."""
    ctx = _make_context(tmp_path)
    try:
        class AlwaysEditModel:
            def __init__(self):
                self.calls = 0

            def __call__(self, messages):
                self.calls += 1
                return [
                    {
                        "name": "apply_ops",
                        "arguments": {
                            "doc_id": "doc1",
                            "client_id": "agent=step_budget",
                            "ops": [{"t": "ins", "at": 0, "text": "x"}],
                        },
                    }
                ]

        model = AlwaysEditModel()
        runner = AgentRunner(model, max_steps=5)
        report = runner.run(ctx, "doc1", "agent=step_budget", "spin")

        assert report.stopped_reason == "max_steps"
        assert report.steps == 5
        # Each step applied one op (distinct seq numbers)
        assert report.ops_applied == 5
        assert len(report.text) == len("Hello agent") + 5
    finally:
        _wipe(tmp_path)


def test_agent_loop_respects_max_ops_budget(tmp_path):
    """A model that sends many ops stops when max_ops is reached."""
    ctx = _make_context(tmp_path)
    try:
        class BatchModel:
            def __call__(self, messages):
                # Send 10 identical inserts per call
                return [
                    {
                        "name": "apply_ops",
                        "arguments": {
                            "doc_id": "doc1",
                            "client_id": "agent=op_budget",
                            "ops": [{"t": "ins", "at": 0, "text": "y"}] * 10,
                        },
                    }
                ]

        # One batch call applies 10 ops (each insert has distinct seq),
        # then the loop stops because max_ops=3 is exceeded.
        runner = AgentRunner(BatchModel(), max_steps=50, max_ops=3)
        report = runner.run(ctx, "doc1", "agent=op_budget", "flood")

        # First call applied all 10 before the loop checked budget
        assert report.stopped_reason == "max_ops"
        assert report.ops_applied == 10
    finally:
        _wipe(tmp_path)


# ----------------------------------------------------------------------
# Tests for tool_apply_ops directly (bypassing the runner)
# ----------------------------------------------------------------------


def test_tool_apply_ops_insert_converges(tmp_path):
    """A single insert through tool_apply_ops lands at the expected index."""
    ctx = _make_context(tmp_path)
    try:
        result = tool_apply_ops(
            ctx, "doc1", "agent=direct",
            [{"t": "ins", "at": 11, "text": "!"}],
        )
        assert result["ok"] is True
        assert result["text"] == "Hello agent!"
        assert result["applied_count"] == 1
        assert result["rev"] >= 1
    finally:
        _wipe(tmp_path)


def test_tool_apply_ops_delete_converges(tmp_path):
    """A delete through tool_apply_ops removes the expected characters."""
    ctx = _make_context(tmp_path)
    try:
        # "Hello agent" is 11 chars. "agent" is indices 6-10 (5 chars).
        # Delete [6, 11) → "Hello "
        result = tool_apply_ops(
            ctx, "doc1", "agent=direct",
            [{"t": "del", "at": 6, "end": 11}],
        )
        assert result["ok"] is True
        assert result["text"] == "Hello "
    finally:
        _wipe(tmp_path)


def test_tool_apply_ops_clamps_indices(tmp_path):
    """Out-of-range indices clamp safely (no panic, no crash)."""
    ctx = _make_context(tmp_path)
    try:
        # Beyond end → append; before start → prepend; end < start → no-op
        result = tool_apply_ops(
            ctx, "doc1", "agent=direct",
            [
                {"t": "ins", "at": 1000, "text": "Z"},  # beyond → append
                {"t": "ins", "at": -5, "text": "A"},    # before → prepend
                {"t": "del", "at": 99, "end": 150},    # beyond → no-op
            ],
        )
        assert result["ok"] is True
        assert result["text"] == "AHello agentZ"
    finally:
        _wipe(tmp_path)


def test_tool_apply_ops_requires_agent_client_id(tmp_path):
    """tool_apply_ops rejects non-agent client_ids (attribution guard)."""
    ctx = _make_context(tmp_path)
    try:
        result = tool_apply_ops(
            ctx, "doc1", "human_user",
            [{"t": "ins", "at": 0, "text": "x"}],
        )
        assert result["ok"] is False
        assert result["error"] == "agent_client_id_required"
    finally:
        _wipe(tmp_path)


def test_tool_apply_ops_missing_doc_returns_404(tmp_path):
    """tool_apply_ops returns 404 for unknown doc_id."""
    ctx = _make_context(tmp_path)
    try:
        result = tool_apply_ops(
            ctx=ctx,
            doc_id="ghost_doc",
            client_id="agent=test",
            ops=[{"t": "ins", "at": 0, "text": "x"}],
        )
        assert result["ok"] is False
        assert result["error"] == "not_found"
        assert result["status"] == 404
    finally:
        _wipe(tmp_path)


# ----------------------------------------------------------------------
# End-to-end loop integration tests
# ----------------------------------------------------------------------


def test_full_loop_interleaves_read_and_edit(tmp_path):
    """A model that reads first, then edits, converges correctly."""
    ctx = _make_context(tmp_path)
    try:
        # Model reads doc, then edits it.
        class ReadThenEdit:
            def __init__(self, ctx):
                self.ctx = ctx
                self.step = 0

            def __call__(self, messages):
                self.step += 1
                if self.step == 1:
                    # First turn: read the current state
                    return [{"name": "read_doc", "arguments": {"doc_id": "doc1"}}]
                elif self.step == 2:
                    # Second turn: edit based on what we read
                    return [
                        {
                            "name": "apply_ops",
                            "arguments": {
                                "doc_id": "doc1",
                                "client_id": "agent=read_then_edit",
                                "ops": [{"t": "ins", "at": 11, "text": " (edited)"}],
                            },
                        }
                    ]
                else:
                    # Third turn: stop
                    return []

        runner = AgentRunner(ReadThenEdit(ctx), max_steps=5)
        report = runner.run(ctx, "doc1", "agent=read_then_edit", "read then edit")

        assert report.stopped_reason == "done"
        # read_doc doesn't bump ops but apply_ops does
        assert report.ops_applied == 1
        assert "Hello agent (edited)" in report.text
        assert len(report.transcript) == 2
    finally:
        _wipe(tmp_path)


def test_full_loop_with_multiple_agents_converges(tmp_path):
    """Two agents editing the same doc converge to one shared state."""
    ctx = _make_context(tmp_path)
    try:
        # Agent A prepends "A", agent B appends "B"
        model_a = ScriptedModel(
            {
                "name": "apply_ops",
                "arguments": {
                    "doc_id": "doc1",
                    "client_id": "agent=first",
                    "ops": [{"t": "ins", "at": 0, "text": "A "}],
                },
            }
        )
        model_b = ScriptedModel(
            {
                "name": "apply_ops",
                "arguments": {
                    "doc_id": "doc1",
                    "client_id": "agent=second",
                    "ops": [{"t": "ins", "at": 13, "text": " B"}],
                },
            }
        )

        runner_a = AgentRunner(model_a)
        report_a = runner_a.run(ctx, "doc1", "agent=first", "prepend A")

        runner_b = AgentRunner(model_b)
        report_b = runner_b.run(ctx, "doc1", "agent=second", "append B")

        # Both agents succeed; document ends with both edits
        assert report_a.stopped_reason == "done"
        assert report_b.stopped_reason == "done"
        # CRDT convergence: both edits present
        text = ctx.hub.state("doc1")["text"]
        assert "A Hello agent" in text
        assert "B" in text
    finally:
        _wipe(tmp_path)