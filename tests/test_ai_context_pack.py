"""Grounding pack (get_context, E18S1): deterministic, size-bounded, isolated.

The pack is a pure function of document state — identical state yields a
byte-identical result (golden-tested). Text is bounded with exact block
spans for anchored edits, versions are a bounded newest-first tail, and the
pack only ever contains the requested document's data (E18S4 isolation).
"""

from __future__ import annotations

import io
import json

import pytest
from docx import Document

from src.ai.schemas import TOOL_NAMES
from src.ai.tools import (
    CONTEXT_MAX_CHARS,
    ToolContext,
    call_tool,
    tool_get_context,
)
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(*paragraphs: str) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "report.docx")
    store.put_content("doc1", _docx_bytes("first line", "", "second line"))
    store.init("doc2", "other.docx")
    store.put_content("doc2", _docx_bytes("SECRET-NEIGHBOR-TEXT"))
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ----------------------------------------------------------------------
# Shape + determinism

def test_get_context_shape_and_golden_determinism(ctx):
    """Identical document state yields byte-identical packs (golden)."""
    pack1 = tool_get_context(ctx, "doc1")
    pack2 = tool_get_context(ctx, "doc1")
    assert json.dumps(pack1, sort_keys=True) == json.dumps(pack2, sort_keys=True)

    assert pack1["ok"] is True
    assert pack1["name"] == "report.docx"
    assert pack1["total_chars"] == len(pack1["text"]) or pack1["truncated"]
    assert "first line" in pack1["text"] and "second line" in pack1["text"]
    assert len(pack1["sha256"]) == 64
    # blocks are [start,end) spans into the text, non-empty lines only
    spans = [(b["start"], b["end"]) for b in pack1["blocks"]]
    assert all(e > s for s, e in spans)
    assert pack1["text"][spans[0][0]:spans[0][1]] == pack1["blocks"][0]["text"] == "first line"


def test_get_context_in_catalog():
    """The grounding pack is part of the advertised tool surface."""
    assert "get_context" in TOOL_NAMES


def test_get_context_block_spans_survive_text_budget_cut(ctx):
    """A block crossing the budget is dropped whole — no partial spans."""
    pack = tool_get_context(ctx, "doc1", max_chars=8)  # cuts inside "first line"
    assert pack["truncated"] is True
    assert len(pack["text"]) == 8
    assert all(b["end"] <= pack["max_chars"] for b in pack["blocks"])


def test_get_context_versions_tail_bounded(ctx):
    """versions_tail is clamped and returned newest-first (metadata only)."""
    store = ctx.store
    store.put_version("doc1", b"v1", author="alice")
    store.put_version("doc1", b"v2-longer-content", author="bob")
    pack = tool_get_context(ctx, "doc1", versions_tail=1)
    assert len(pack["versions"]) == 1
    assert pack["versions"][0]["author"] == "bob"  # newest first
    assert set(pack["versions"][0]) == {"ts", "author", "size"}


# ----------------------------------------------------------------------
# Bounds + typed errors

def test_get_context_rejects_out_of_bounds_arguments(ctx):
    assert tool_get_context(ctx, "doc1", max_chars=0)["error"] == "bad_request"
    assert tool_get_context(ctx, "doc1", max_chars=CONTEXT_MAX_CHARS + 1)["error"] == "bad_request"
    assert tool_get_context(ctx, "doc1", versions_tail=-1)["error"] == "bad_request"
    assert tool_get_context(ctx, "doc1", versions_tail=99)["error"] == "bad_request"
    assert tool_get_context(ctx, "doc1", max_chars="lots")["error"] == "bad_request"


def test_get_context_unknown_doc_is_typed_not_found(ctx):
    result = tool_get_context(ctx, "docNOPE")
    assert result["ok"] is False and result["error"] == "not_found"


def test_get_context_isolation_neighbor_content_never_leaks(ctx):
    """E18S4: the pack for doc1 must never contain doc2's content —
    cross-document context is structurally impossible, not just filtered."""
    pack = tool_get_context(ctx, "doc1")
    assert "SECRET-NEIGHBOR-TEXT" not in json.dumps(pack)
    # even the sha256 differs from the neighbor's pack
    neighbor = tool_get_context(ctx, "doc2")
    assert pack["sha256"] != neighbor["sha256"]


def test_get_context_via_call_tool_and_pack_feeds_edit_indices(ctx):
    """End-to-end: discovery -> pack -> apply_ops at a block span lands."""
    pack = call_tool(ctx, "get_context", {"doc_id": "doc1"})
    assert pack["ok"] is True
    anchor = pack["blocks"][0]["end"]  # end of "first line"
    edit = call_tool(ctx, "apply_ops", {
        "doc_id": "doc1", "client_id": "agent=alfie",
        "ops": [{"t": "ins", "at": anchor, "text": "!"}],
    })
    assert edit["ok"] is True
    after = call_tool(ctx, "get_context", {"doc_id": "doc1"})
    assert "first line!" in after["text"]
