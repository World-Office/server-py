"""Agent edits preserve document integrity — property-based (eval harness).

The core eval-harness invariant (spec: agent-eval-harness): an agent's op
sequence applied through the tool surface produces exactly the text a plain
reference simulation of the same edit sequence produces, no content lost,
no 500s — under Hypothesis-generated edit storms, interleaved with a human
editing via full-text sync.
"""

from __future__ import annotations

import io

from docx import Document
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.ai.tools import ToolContext, tool_apply_ops
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir

SETTINGS = settings(
    max_examples=50,
    deadline=None,
    # each example builds its own store inside the test body, so sharing
    # the tmp_path directory across examples is safe
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

BASE = "Hello agent world"

ins_strategy = st.fixed_dictionaries({
    "t": st.just("ins"),
    "at": st.integers(min_value=0, max_value=len(BASE) + 3),
    "text": st.text(alphabet="abcXYZ .!", min_size=1, max_size=5),
})
del_strategy = st.fixed_dictionaries({
    "t": st.just("del"),
    "at": st.integers(min_value=0, max_value=len(BASE) + 3),
    "end": st.one_of(st.none(), st.integers(min_value=0, max_value=len(BASE) + 6)),
})
edit_strategy = st.one_of(ins_strategy, del_strategy)


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _reference_edit(text: str, edit: dict) -> str:
    """Plain-string simulation of one clamped agent edit (the oracle)."""
    if edit["t"] == "ins":
        at = max(0, min(edit["at"], len(text)))
        return text[:at] + edit["text"] + text[at:]
    at = max(0, min(edit["at"], len(text)))
    end = edit["end"] if isinstance(edit["end"], int) else at + 1
    end = max(at, min(end, len(text)))
    return text[:at] + text[end:]


def _make_ctx(tmp_path) -> ToolContext:
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "p.docx")
    store.put_content("doc1", _docx_bytes(BASE))
    return ToolContext(store=store, hub=CollabHub())


def _wipe(tmp_path):
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


@SETTINGS
@given(edits=st.lists(edit_strategy, min_size=1, max_size=25))
def test_agent_edit_storm_matches_reference_simulation(tmp_path, edits):
    """Long multi-step agent loop: text == sequence of valid ops applied,
    nothing lost (TC-E22-01/TC-E22-07/TC-E15-01)."""
    ctx = _make_ctx(tmp_path)
    try:
        expected = BASE
        for edit in edits:
            expected = _reference_edit(expected, edit)
        result = tool_apply_ops(ctx, "doc1", "agent=storm", edits)
        assert result["ok"] is True
        assert result["text"] == expected
        # hub state agrees with the tool reply (single source of truth)
        assert ctx.hub.state("doc1")["text"] == expected
    finally:
        _wipe(tmp_path)


@SETTINGS
@given(human_bits=st.lists(st.text(alphabet="HWabcd .!", min_size=1, max_size=4),
                           min_size=1, max_size=6))
def test_agent_and_human_edits_merge_without_loss(tmp_path, human_bits):
    """Interleaved human (full-text sync) + agent (ops) edits: everything
    either party wrote survives (TC-E15-03)."""
    ctx = _make_ctx(tmp_path)
    try:
        hub = ctx.hub
        hub.ensure("doc1", BASE)
        human_text = BASE
        for i, bit in enumerate(human_bits):
            # agent appends a marker at the end
            marker = f"[A{i}]"
            agent = tool_apply_ops(
                ctx, "doc1", "agent=interleave",
                [{"t": "ins", "at": len(hub.state("doc1")["text"]), "text": marker}],
            )
            assert agent["ok"] is True
            # human syncs a full text that keeps everything seen so far
            human_text = hub.state("doc1")["text"] + bit
            hub.sync_text("doc1", "human-1", human_text)
            current = hub.state("doc1")["text"]
            # no lost work: every marker and every human bit still present
            for j in range(i + 1):
                assert f"[A{j}]" in current
            assert bit in current
    finally:
        _wipe(tmp_path)


def test_malformed_agent_batches_never_break_the_hub(tmp_path):
    """Hostile batch: garbage ops mixed with valid ones — hub rejects the
    garbage, applies the valid, stays alive (TC-E17-01 style)."""
    ctx = _make_ctx(tmp_path)
    try:
        hostile = [
            {"t": "ins"},                      # missing fields
            {"t": "ins", "at": "x", "text": 1},
            {"t": "delete", "s": 42, "ids": "not-a-list"},
            "not-even-a-dict",
            {"t": "ins", "at": 0, "text": "VALID"},  # the one good edit
            {"t": "ins", "at": -5, "text": "clamp"},
            None,
        ]
        result = tool_apply_ops(ctx, "doc1", "agent=hostile", hostile)
        assert result["ok"] is True
        text = result["text"]
        assert "VALID" in text and "clamp" in text
        assert "Hello agent world" in text
        # hub still fully functional afterwards
        follow = tool_apply_ops(ctx, "doc1", "agent=hostile",
                                [{"t": "ins", "at": 0, "text": "OK "}])
        assert follow["ok"] is True and follow["text"].startswith("OK ")
    finally:
        _wipe(tmp_path)
