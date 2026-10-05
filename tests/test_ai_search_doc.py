"""Cited retrieval (search_doc, E18S3): deterministic passage ranking.

The answering is the model's job; the server owns the verifiable citation
layer: spans into the text (feed them straight into set_span anchors),
relevance-then-position ordering, and document/rev/sha256 refs. Identical
document + query always yield identical matches.
"""

from __future__ import annotations

import io
import json

import pytest
from docx import Document

from src.ai.schemas import TOOL_NAMES
from src.ai.tools import SEARCH_MAX_PASSAGES, ToolContext, tool_search_doc
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
    store.init("doc1", "findings.docx")
    store.put_content("doc1", _docx_bytes(
        "The catalyst ratio was measured at dawn.",
        "Unrelated boilerplate line.",
        "Catalyst catalyst catalyst — the ratio again.",
        "Final notes on stability.",
    ))
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def test_search_doc_is_in_catalog():
    assert "search_doc" in TOOL_NAMES


def test_ranking_relevance_then_position_and_determinism(ctx):
    r1 = tool_search_doc(ctx, "doc1", "catalyst ratio")
    r2 = tool_search_doc(ctx, "doc1", "catalyst ratio")
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)  # golden determinism
    assert r1["ok"] is True
    top = r1["matches"][0]
    assert top["score"] == 4  # 3x catalyst + 1x ratio in the dense line
    # span coordinates cut exactly the cited passage out of the text
    text = ctx.hub.ensure("doc1", "").snapshot()["text"]
    assert text[top["start"]:top["end"]] == top["text"]
    # the empty line is never a match; unrelated line absent entirely
    texts = [m["text"] for m in r1["matches"]]
    assert "Unrelated boilerplate line." not in texts


def test_refs_carry_doc_rev_sha256(ctx):
    result = tool_search_doc(ctx, "doc1", "stability")
    pack = ctx.hub.ensure("doc1", "").snapshot()["text"]
    import hashlib
    assert result["rev"] == ctx.hub.ensure("doc1", "").snapshot()["rev"]
    assert result["sha256"] == hashlib.sha256(pack.encode("utf-8")).hexdigest()
    assert result["matches"][0]["text"] == "Final notes on stability."


def test_no_match_is_empty_ok_result(ctx):
    result = tool_search_doc(ctx, "doc1", "zzz-nothing")
    assert result["ok"] is True and result["matches"] == []


def test_bounds_and_typed_errors(ctx):
    assert tool_search_doc(ctx, "docNOPE", "x")["error"] == "not_found"
    assert tool_search_doc(ctx, "doc1", "")["error"] == "bad_request"
    assert tool_search_doc(ctx, "doc1", "   ")["error"] == "bad_request"
    assert tool_search_doc(ctx, "doc1", "x" * 513)["error"] == "bad_request"
    assert tool_search_doc(ctx, "doc1", "x", max_passages=0)["error"] == "bad_request"
    assert tool_search_doc(ctx, "doc1", "x", max_passages=SEARCH_MAX_PASSAGES + 1)["error"] == "bad_request"


def test_max_passages_is_respected(ctx):
    result = tool_search_doc(ctx, "doc1", "the", max_passages=2)
    assert len(result["matches"]) == 2
    assert [m["score"] for m in result["matches"]] == sorted(
        (m["score"] for m in result["matches"]), reverse=True)
