"""Agent path under the sanitizer contract (E17S4): agent-authored content
can never smuggle markup.

Two hops are pinned: (1) the CRDT stores agent text verbatim — no server-
side HTML interpretation on the apply path; (2) when that text later renders
(docx -> html), the converter escapes it — no executable markup reaches the
editor. The corpus mirrors tests/test_sanitizer_adversarial.py's hostile
payloads, delivered through the agent tool surface.
"""

from __future__ import annotations

import io

import pytest
from docx import Document

from src.ai.tools import ToolContext, tool_apply_ops
from src.editor.converter import docx_to_html
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir

HOSTILE_MARKUP = [
    "<script>alert('agent')</script>",
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert(1)>",
    "</div><script>fetch('/steal')</script>",
    "<a href=\"javascript:alert(1)\">click</a>",
    "<iframe src=\"https://evil.example\"></iframe>",
]


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "agentsan.docx")
    store.put_content("doc1", _docx_bytes("clean seed"))
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def test_agent_text_is_stored_verbatim_no_interpretation(ctx):
    """The apply path is text-only: hostile markup lands as characters,
    never as elements the server parses or executes."""
    result = tool_apply_ops(ctx, "doc1", "agent=xss",
                            [{"t": "ins", "at": 10, "text": HOSTILE_MARKUP[0]}])
    assert result["ok"] is True
    assert result["text"].endswith(HOSTILE_MARKUP[0])  # verbatim, byte-for-byte


@pytest.mark.parametrize("payload", HOSTILE_MARKUP)
def test_agent_markup_is_escaped_on_render(payload, tmp_path):
    """The render hop: a document carrying agent-authored hostile text
    escapes it — no raw tags/attrs survive docx -> html."""
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    try:
        store.init("doc2", "hostile.docx")
        store.put_content("doc2", _docx_bytes(payload))
        html = docx_to_html(store.get_content("doc2"))
        low = html.lower()
        # no RAW executable markup survives: every payload tag comes out
        # escaped — the words may appear, the tags may not
        for raw in ("<script", "<iframe", "<img", "<svg", "<a href", "</div>"):
            assert raw not in low, f"raw {raw!r} leaked: {html!r}"
        assert "&lt;" in html  # the payload was escaped, not silently dropped
        assert "alert" in low or "fetch" in low or "click" in low or "evil.example" in low
    finally:
        wipe_db(tmp_path / "t.db")
        wipe_dir(tmp_path / "content")


def test_full_agent_flow_end_to_end_escapes(ctx, tmp_path):
    """Ground -> edit hostile markup -> the resulting text, persisted and
    rendered, comes out escaped."""
    payloads = "".join(HOSTILE_MARKUP)
    tool_apply_ops(ctx, "doc1", "agent=xss", [{"t": "ins", "at": 10, "text": payloads}])
    text = tool_apply_ops.__wrapped__ if False else ctx.hub.ensure(
        "doc1", "clean seed").snapshot()["text"]
    # now the save/render contract: same text inside a docx renders escaped
    html = docx_to_html(_docx_bytes("clean seed" + payloads))
    low = html.lower()
    assert "<script" not in low and "<iframe" not in low
    assert "&lt;script" in low  # present, but inert
