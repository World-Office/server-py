"""Anchored agent edits (E18S2): set_span + expected CAS — precision beats rewrite.

Anchors resolve against the live text at apply time; ``expected`` is the
compare-and-swap guard — a stale grounding pack can never clobber a
document that moved under the agent (typed 412 anchor_mismatch). A bad
anchor aborts the whole call with a typed 400 and applies nothing.
"""

from __future__ import annotations

import io

import pytest
from docx import Document

from src.ai.tools import ToolContext, tool_apply_ops, tool_lock, tool_read_doc
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir

DOC = "alpha bravo\ncharlie delta"


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "anchors.docx")
    store.put_content("doc1", _docx_bytes(DOC))  # 12 + \n + 13 chars
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _edit(ctx, ops):
    return tool_apply_ops(ctx, doc_id="doc1", client_id="agent=anchor", ops=ops)


def _text(ctx):
    """Grounded read — seeds the hub the same way every tool does."""
    return tool_read_doc(ctx, "doc1")["text"]


# ----------------------------------------------------------------------
# set_span: replace / delete spans, verified round-trip

def test_set_span_replaces_a_block_and_survives_roundtrip(ctx):
    result = _edit(ctx, [{"t": "set_span", "start": 12, "end": 25, "text": "one two"}])
    assert result["ok"] is True and result["applied_count"] == 2  # del + ins
    assert _text(ctx) == "alpha bravo\none two"
    # round-trip: the same coordinates from a fresh grounding read now hold the new text
    now = _text(ctx)
    assert now[12:19] == "one two"


def test_set_span_with_empty_text_is_pure_deletion(ctx):
    result = _edit(ctx, [{"t": "set_span", "start": 0, "end": 6, "text": ""}])
    assert result["ok"] is True
    assert _text(ctx) == "bravo\ncharlie delta"


def test_expected_mismatch_is_typed_412_and_applies_nothing(ctx):
    result = _edit(ctx, [{"t": "set_span", "start": 0, "end": 5, "expected": "stale", "text": "x"}])
    assert result["ok"] is False
    assert result["error"] == "anchor_mismatch" and result["status"] == 412
    assert result["expected"] == "stale" and result["actual"] == "alpha"
    assert _text(ctx) == DOC  # untouched


def test_expected_match_edits_exactly_what_the_pack_showed(ctx):
    pack_text = _text(ctx)  # simulate the agent grounding first
    result = _edit(ctx, [{
        "t": "set_span", "start": 6, "end": 11,
        "expected": pack_text[6:11], "text": "BRAVO",
    }])
    assert result["ok"] is True
    assert _text(ctx) == "alpha BRAVO\ncharlie delta"


def test_expected_on_plain_ins_and_del(ctx):
    ok = _edit(ctx, [{"t": "ins", "at": 0, "expected": "alpha", "text": "> "}])
    assert ok["ok"] is True and _text(ctx).startswith("> alpha")
    ok = _edit(ctx, [{"t": "del", "at": 0, "end": 2, "expected": "> "}])
    assert ok["ok"] is True and _text(ctx) == DOC
    bad = _edit(ctx, [{"t": "ins", "at": 0, "expected": "wrong", "text": "x"}])
    assert bad["error"] == "anchor_mismatch" and bad["status"] == 412


def test_bad_anchor_aborts_the_rest_of_the_batch(ctx):
    """Sequential semantics: an anchor error aborts the REMAINING ops; the
    result carries exactly what landed before the failure, so the agent
    never has to guess."""
    result = _edit(ctx, [
        {"t": "ins", "at": 0, "text": "ok-"},                       # lands
        {"t": "set_span", "start": 10, "end": 9999, "text": "x"},  # bad anchor
        {"t": "ins", "at": 0, "text": "never"},                    # aborted
    ])
    assert result["ok"] is False
    assert result["error"] == "bad_anchor" and result["status"] == 400
    assert result["got"] == {"start": 10, "end": 9999}
    assert result["applied_count"] == 1  # only the first op landed
    assert _text(ctx) == "ok-" + DOC


def test_bad_anchor_types(ctx):
    assert _edit(ctx, [{"t": "set_span", "start": "a", "end": 2, "text": "x"}])["error"] == "bad_anchor"
    assert _edit(ctx, [{"t": "set_span", "start": 5, "end": 2, "text": "x"}])["error"] == "bad_anchor"
    assert _edit(ctx, [{"t": "set_span", "start": -1, "end": 2, "text": "x"}])["error"] == "bad_anchor"
    assert _edit(ctx, [{"t": "set_span", "text": 7}])["error"] == "bad_anchor"


def test_anchored_ops_see_text_after_previous_ops_in_same_call(ctx):
    """Sequential semantics documented in the catalog: index 0 after an
    insert at 0 refers to the text WITH the insertion already there."""
    result = _edit(ctx, [
        {"t": "ins", "at": 0, "text": "X"},
        {"t": "set_span", "start": 0, "end": 1, "expected": "X", "text": "Y"},
    ])
    assert result["ok"] is True
    assert _text(ctx) == "Y" + DOC


def test_anchor_flow_with_lock_still_enforces_409(ctx):
    tool_lock(ctx, "doc1", "lock", token="human-lock", user="ada")
    result = _edit(ctx, [{"t": "set_span", "start": 0, "end": 1, "text": "z"}])
    assert result["error"] == "lock_mismatch" and result["status"] == 409
    ok = tool_apply_ops(ctx, "doc1", "agent=anchor",
                        [{"t": "set_span", "start": 0, "end": 1, "text": "z"}],
                        lock_token="human-lock")
    assert ok["ok"] is True


def test_search_to_anchor_pipeline(ctx):
    """The E18S2+S3 pipeline: search_doc passage -> set_span with expected."""
    from src.ai.tools import tool_search_doc

    hit = tool_search_doc(ctx, "doc1", "charlie", max_passages=1)
    assert hit["ok"] is True
    match = hit["matches"][0]
    assert match["text"] == "charlie delta" and match["score"] >= 1
    result = _edit(ctx, [{
        "t": "set_span",
        "start": match["start"], "end": match["end"],
        "expected": match["text"], "text": "charlie echo",
    }])
    assert result["ok"] is True
    assert "charlie echo" in _text(ctx)
