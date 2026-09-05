"""Edge units across the hostile-input + review + MCP surface.

Three target areas, exercised unit-style (no HTTP, no network, no sleeps):

1. **sanitize.py** — direct unit coverage of the sanitizer's private
   guardrails: ``_sanitize_style`` (whitelist + reject-all unsafe constructs
   + escape/entity obfuscation + length cap), ``_is_safe_srcset`` (per-
   candidate URL verdict) and ``handle_entityref`` (content-suppression
   scope: an entity inside a dropped ``<script>/<style>/<iframe>`` must not
   leak into the output).

2. **review.py** — the typed error surface of ``reject_agent_ops``:
   ``unknown_rev``, ``not_an_agent_op`` (human op and non-dict log entry),
   ``nothing_to_restore`` (delete referencing ids with no items), and
   ``compile_failed`` (inverse cannot be compiled) plus the idempotent
   ``already_reverted`` path. A positive control guards that the machinery
   really restores text, so the error assertions have teeth.

3. **mcp.py`` ``_call`` — the tools/call adapter: a malformed ``params`` is
   an ``isError`` *result* (not a JSON-RPC error) per the MCP spec; unknown
   tools, non-object arguments and the ``agents_enabled=False`` kill switch
   all fail closed with typed results, and a successful call returns
   ``isError=False``.

The safety verdict for sanitizer tests is asserted against the *public*
output (document text / attribute emission), never against private state
exclusively — mirroring the suite's convention of judging the re-parsed
document.
"""

from __future__ import annotations

import io
import json

import pytest
from docx import Document
from hypothesis import given, settings
from hypothesis import strategies as st

from src.ai.mcp import McpServer, _tool_result
from src.ai.review import reject_agent_ops
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.editor.sanitize import _is_safe_srcset, _sanitize_style, _XSSSanitizer, sanitize_html
from src.lib.store import DocumentStore, wipe_db, wipe_dir

# ----------------------------------------------------------------------
# Shared helpers / fixtures
# ----------------------------------------------------------------------


def _docx_bytes(text: str = "MCP doc") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _hub_with(text: str = "Hello agent world"):
    """A fresh hub (no shared state) seeded from *text* (rev 1 = seed op)."""
    hub = CollabHub()
    hub.ensure("doc1", text)
    return hub


def _agent_insert(hub, text: str, b: int = 900) -> int:
    """Apply an attributable agent insert; return its revision."""
    reply = hub.apply_ops("doc1", "agent=alfie", [
        {"t": "insert", "s": "agent=alfie", "b": b, "n": len(text),
         "chars": text, "originSite": "", "originSeq": 0},
    ])
    return reply["rev"]


@pytest.fixture
def mcp_server(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "mcp.docx")
    store.put_content("doc1", _docx_bytes())
    ctx = ToolContext(store=store, hub=CollabHub())
    yield McpServer(ctx)
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ======================================================================
# 1. sanitize.py: _sanitize_style
# ======================================================================


def test_sanitize_style_keeps_whitelisted_properties():
    """Whitelisted text-formatting declarations survive verbatim."""
    out = _sanitize_style("color: red; font-weight: bold; text-align: center;")
    assert out == "color: red; font-weight: bold; text-align: center;"


@pytest.mark.parametrize(
    "style",
    [
        "background: url(javascript:alert(1))",
        "width: expression(alert(1))",
        "background-image: url(data:image/svg+xml,<svg onload=alert(1)>)",
        "background: url(https://evil.example/steal.png)",
        "cursor: javascript:alert(1)",
        "behavior: url(#default#time2)",
        "background: -moz-binding url(x)",
        "color: red; @import url(css.txt)",
        "position: fixed; color: red",
        "position: absolute",
    ],
    ids=[
        "url-js", "ie-expression", "url-svg-xss", "url-http-exfil",
        "javascript-scheme", "behavior", "moz-binding", "at-import", "position-fixed",
        "position-absolute",
    ],
)
def test_sanitize_style_rejects_unsafe_constructs_entirely(style: str):
    """Any style containing an unsafe construct is rejected whole (None).

    ``position: fixed/absolute`` counts as unsafe (UI-hijack), as do
    ``url(...)`` (remote/exfil loading), IE ``expression``, script schemes,
    ``behavior``, ``-moz-binding`` and ``@import`` — none of the safe
    declarations around them may be rescued.
    """
    assert _sanitize_style(style) is None


def test_sanitize_style_drops_non_whitelisted_declarations_keeps_safe_ones():
    """Non-whitelisted props (display/grid/etc.) are dropped per-declaration;
    the surviving safe declarations are rejoined."""
    out = _sanitize_style("color: red; display: none; font-size: 12px; width: 100%")
    assert out == "color: red; font-size: 12px;"


def test_sanitize_style_rejects_all_obfuscated_declaration():
    """CSS escapes and HTML entities hide tokens from string checks, so any
    declaration whose value contains ``\\`` or ``&`` is rejected outright."""
    assert _sanitize_style("color: \\65 xpression;") is None
    assert _sanitize_style("font-family: '\\6a avascript'") is None
    # an obfuscated declaration is dropped while a clean sibling survives
    out = _sanitize_style("color: red; font-size: 10&amp;px")
    assert out == "color: red;"
    assert "font-size" not in out


def test_sanitize_style_rejects_oversized_result():
    """A style longer than 512 chars is dropped (history/DoS guard)."""
    assert _sanitize_style(f"color: {'a' * 600};") is None
    big_but_safe = _sanitize_style("color: " + "a" * 100)
    assert big_but_safe is not None


def test_sanitize_style_empty_and_unknown_props_return_none():
    """A style with no surviving whitelisted declaration is None, not ''."""
    assert _sanitize_style("") is None
    assert _sanitize_style("   ") is None
    assert _sanitize_style("novalue") is None
    assert _sanitize_style("width: 100px; height: 50px") is None


# ======================================================================
# 1b. sanitize.py: _is_safe_srcset
# ======================================================================


@pytest.mark.parametrize(
    ("srcset", "expected"),
    [
        # safe: absolute / root-relative / explicit-relative / https
        ("/img/photo.png 1x, /img/photo@2x.png 2x", True),
        ("./pic.png 1x", True),
        ("https://cdn.ok.example/a.png 1x", True),
        ("http://ok.example/a.png 1x, https://ok.example/b.png 2x", True),
        # NOTE: existing behaviour — a bare relative filename (no leading
        # ``/`` or ``./``) is treated as an unsafe candidate and fails the
        # whole srcset; the sanitizer is deliberately conservative.
        ("photo.png 1x", False),
        # unsafe: one hostile candidate poisons the whole srcset
        ("javascript:alert(1) 1x", False),
        ("/ok.png 1x, javascript:alert(1) 2x", False),
        ("VBScrIPT:msgbox(1) 1x", False),
        ("data:text/html,<script>alert(1)</script> 1x", False),
        # NOTE: existing behaviour — a data:image/ candidate embeds a comma
        # (``data:image/png;base64,...``) that the naive comma-split breaks
        # into two candidates, so even a safe inline-image data URI fails.
        ("data:image/png;base64,iVBOR 1x", False),
    ],
    ids=[
        "root-relative-pair", "dot-relative", "https", "http+https",
        "bare-relative-name", "js-scheme", "mixed-hostile-second",
        "vbscript-case", "data-html", "data-image-comma-split",
    ],
)
def test_is_safe_srcset_verdicts(srcset: str, expected: bool):
    """Every candidate URL in a srcset must be a safe image URL — one bad
    candidate fails the whole attribute, whitespace candidates are skipped."""
    assert _is_safe_srcset(srcset) is expected


def test_is_safe_srcset_empty_and_whitespace_only_inputs():
    """Empty / whitespace-only srcsets are vacuously safe (no candidates)."""
    assert _is_safe_srcset("") is True
    assert _is_safe_srcset("   ") is True
    assert _is_safe_srcset("  ,  ,  ") is True


@given(value=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200))
@settings(max_examples=100, deadline=None)
def test_is_safe_srcset_never_raises_and_returns_bool(value: str):
    """Property: any arbitrary srcset string yields a boolean, never throws.

    A hostile agent can submit anything; the guard must be total.
    """
    result = _is_safe_srcset(value)
    assert isinstance(result, bool)


# ======================================================================
# 1c. sanitize.py: handle_entityref + content suppression
# ======================================================================


def test_handle_entityref_emits_entity_when_not_suppressed():
    """Outside a dropped element, character references pass through."""
    s = _XSSSanitizer()
    s.handle_entityref("amp")
    s.handle_entityref("lt")
    assert s.get_output() == "&amp;&lt;"


def test_handle_entityref_is_silenced_inside_suppressed_unsafe_content():
    """Content of a dropped <script>/<style>/<iframe> is suppressed, and
    character references inside it must not leak out either (entityrefs
    re-emitted from a dead script would otherwise surface as visible text).
    """
    # direct: the suppression depth closes the entityref outlet
    s = _XSSSanitizer()
    s._suppress_depth = 1
    s.handle_entityref("amp")
    assert s.get_output() == ""
    # through the public API: entity inside <script> vanishes, following
    # real document text survives
    out = sanitize_html("<script>&amp;&#60;script&#62;</script><p>kept</p>")
    assert "&amp" not in out
    assert "<p>kept</p>" in out
    # entity outside a dropped element is preserved
    assert sanitize_html("<p>a &amp; b</p>") == "<p>a &amp; b</p>"


# ======================================================================
# 2. review.py: reject_agent_ops error surface
# ======================================================================


def test_reject_insert_restores_exact_pre_op_text():
    """Positive control: the reject machinery really works, so the error
    assertions below have teeth."""
    hub = _hub_with()
    rev = _agent_insert(hub, "XYZ")
    result = reject_agent_ops(hub, "doc1", [rev])
    assert result["applied_any"] is True
    assert result["text"] == "Hello agent world"
    assert result["rejected"][0]["ok"] is True
    assert result["rejected"][0]["error"] is None


def test_reject_unknown_rev_is_typed_error():
    result = reject_agent_ops(_hub_with(), "doc1", [999])
    assert result["applied_any"] is False
    assert result["rejected"][0]["ok"] is False
    assert result["rejected"][0]["error"] == "unknown_rev"
    assert result["text"] == "Hello agent world"


def test_reject_human_op_is_not_an_agent_op_error():
    """Only ops attributable to an agent site are revertible; a human insert
    at the target revision is refused with a typed error."""
    hub = _hub_with()
    hub.apply_ops("doc1", "human-1", [
        {"t": "insert", "s": "human-1", "b": 950, "n": 1, "chars": "Q",
         "originSite": "", "originSeq": 0},
    ])
    result = reject_agent_ops(hub, "doc1", [2])
    assert result["rejected"][0]["ok"] is False
    assert result["rejected"][0]["error"] == "not_an_agent_op"
    assert result["text"] == "QHello agent world"


def test_reject_non_dict_log_entry_is_not_an_agent_op_error():
    """A corrupt (non-dict) log slot at the target rev is refused, never
    raises — the reviewer stays up on dirty input."""
    hub = _hub_with()
    hub.ensure("doc1").log.append("garbage")
    hub.ensure("doc1").rev += 1
    result = reject_agent_ops(hub, "doc1", [2])
    assert result["rejected"][0]["ok"] is False
    assert result["rejected"][0]["error"] == "not_an_agent_op"


def test_reject_delete_with_no_materialized_items_is_nothing_to_restore():
    """A logged agent delete whose ids have no items in the CRDT has no text
    to bring back — reject reports ``nothing_to_restore`` instead of
    emitting an empty inverse insert. (Unreachable through the hub's normal
    apply path — a delete of unknown ids is parked, not logged — so it is
    pinned here by seeding the log directly.)
    """
    hub = _hub_with()
    state = hub.ensure("doc1")
    ghost = {"t": "delete", "s": "agent=alfie", "ids": [["agent=alfie", 4242]]}
    state.log.append(ghost)
    state.rev += 1
    result = reject_agent_ops(hub, "doc1", [2])
    assert result["rejected"][0]["ok"] is False
    assert result["rejected"][0]["error"] == "nothing_to_restore"
    assert result["text"] == "Hello agent world"


def test_reject_delete_compile_failure_is_compile_failed_error(monkeypatch):
    """If the inverse re-insert cannot be compiled, the rejection reports
    ``compile_failed`` and applies nothing (defensive branch reached by
    forcing ``compile_text_edit`` to fail)."""
    hub = _hub_with()
    state = hub.ensure("doc1")
    seed = state.log[0]
    start = seed["b"] + seed["chars"].index("agent")
    ids = [[seed["s"], start + i] for i in range(len("agent"))]
    hub.apply_ops("doc1", "agent=alfie", [{"t": "delete", "s": "agent=alfie", "ids": ids}])

    monkeypatch.setattr("src.ai.tools.compile_text_edit", lambda *args, **kwargs: None)
    result = reject_agent_ops(hub, "doc1", [2])
    assert result["rejected"][0]["ok"] is False
    assert result["rejected"][0]["error"] == "compile_failed"
    assert result["applied_any"] is False
    assert result["text"] == "Hello  world"  # untouched


def test_reject_same_rev_twice_is_already_reverted():
    """Rejecting an already-rejected rev is a graceful no-op, not an error
    the caller must treat as fatal."""
    hub = _hub_with()
    rev = _agent_insert(hub, "XYZ")
    first = reject_agent_ops(hub, "doc1", [rev])
    assert first["applied_any"] is True
    second = reject_agent_ops(hub, "doc1", [rev])
    assert second["applied_any"] is False
    assert second["rejected"][0]["ok"] is False
    assert second["rejected"][0]["error"] == "already_reverted"
    assert second["text"] == "Hello agent world"


# ======================================================================
# 3. mcp.py: McpServer._call
# ======================================================================


def test_call_non_object_params_is_bad_request_result(mcp_server):
    """A malformed tools/call params is an isError *result* the model can
    read (MCP spec), not a JSON-RPC transport error."""
    for bad in ("garbage", None, 42, ["list"]):
        result = mcp_server._call(bad)
        assert result["isError"] is True
        payload = json.loads(result["content"][0]["text"])
        assert payload["ok"] is False
        assert payload["error"] == "bad_request"
        assert payload["status"] == 400
        assert payload["hint"] == "params must be an object"


def test_call_unknown_tool_is_error_result_not_protocol_error(mcp_server):
    """An unknown tool name surfaces as an error *result* (isError=True) so
    the model can react, and the JSON-RPC frame itself stays a result."""
    result = mcp_server._call({"name": "definitely_not_a_tool", "arguments": {}})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is False
    assert payload["status"] == 404
    assert payload["error"] == "unknown_tool"
    # end-to-end: the frame has no JSON-RPC "error" member (a result present)
    frame = mcp_server.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "no_such_tool", "arguments": {}}}
    )
    assert "result" in frame and "error" not in frame
    assert frame["result"]["isError"] is True


def test_call_agents_disabled_fails_closed(mcp_server):
    """DOCSERVER_AGENTS=0 (agents_enabled=False) makes every call a typed
    ``agents_disabled`` error even for a valid, existing document."""
    mcp_server.ctx.agents_enabled = False
    result = mcp_server._call({"name": "read_doc", "arguments": {"doc_id": "doc1"}})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is False
    assert payload["status"] == 403
    assert payload["error"] == "agents_disabled"


def test_call_non_object_arguments_is_bad_request(mcp_server):
    """arguments must be a JSON object; anything else is a typed 400."""
    result = mcp_server._call({"name": "read_doc", "arguments": "nope"})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "bad_request"
    assert payload["status"] == 400


def test_call_successful_tool_invocation_returns_is_error_false(mcp_server):
    """A valid tool call resolves to an isError=False result with the
    tool envelope as the text content."""
    result = mcp_server._call({"name": "read_doc", "arguments": {"doc_id": "doc1"}})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["name"] == "mcp.docx"
    assert payload["text"] == "MCP doc"


def test_call_result_envelope_shape():
    """_tool_result always wraps the envelope as the single text block and
    maps ok/not-ok onto isError -- the contract tools/call clients rely on."""
    ok = _tool_result({"ok": True, "error": None}, is_error=False)
    err = _tool_result({"ok": False, "error": "x", "status": 500}, is_error=True)
    assert ok["isError"] is False
    assert err["isError"] is True
    for r in (ok, err):
        assert len(r["content"]) == 1
        assert r["content"][0]["type"] == "text"
        json.loads(r["content"][0]["text"])  # always valid JSON
