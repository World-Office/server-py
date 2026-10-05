"""Agent tool surface: discovery, contracts, and hostile-input hardening.

Covers the agent-tool-surface spec: tool discovery (read_doc / apply_ops /
get_versions / lock / presence), model-agnostic schemas, the WOPI lock
control plane (409 lock-mismatch for agents too), typed not-found results,
and rejection of malformed input without crashing anything.
"""

from __future__ import annotations

import io

import pytest
from docx import Document

from src.ai import AGENT_PREFIX
from src.ai.schemas import TOOL_CATALOG, TOOL_NAMES
from src.ai.tools import (
    MAX_OPS_PER_CALL,
    ToolContext,
    call_tool,
    compile_text_edit,
    tool_apply_ops,
    tool_lock,
    tool_presence,
    tool_read_doc,
)
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str = "Hello agent") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "hello.docx")
    store.put_content("doc1", _docx_bytes())
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ----------------------------------------------------------------------
# Discovery + schemas (model-agnostic)

def test_tool_catalog_has_the_seven_spec_tools():
    assert set(TOOL_NAMES) == {
        "read_doc", "apply_ops", "get_versions", "get_context", "search_doc", "lock", "presence"
    }
    for tool in TOOL_CATALOG:
        assert tool["name"] and tool["description"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and "properties" in schema
        # model-agnostic: no vendor-specific fields anywhere in the catalog
        assert "claude" not in str(schema).lower()
        assert "openai" not in str(schema).lower()


def test_registry_matches_catalog_exactly():
    # call_tool and the advertised catalog must never drift apart
    for name in TOOL_NAMES:
        result = call_tool(ToolContext(store=None, hub=None), name, None)
        # with a None store every real tool fails, but NONE may be "unknown_tool"
        assert result["error"] != "unknown_tool"


# ----------------------------------------------------------------------
# read_doc

def test_read_doc_returns_text_metadata_and_lock(ctx):
    result = tool_read_doc(ctx, "doc1")
    assert result["ok"] is True
    assert result["name"] == "hello.docx"
    assert "Hello" in result["text"]
    assert result["lock"] == ""
    assert isinstance(result["ops"], list)


def test_read_doc_unknown_id_is_not_found_never_error(ctx):
    result = tool_read_doc(ctx, "missing-doc")
    assert result == {
        "ok": False, "error": "not_found", "status": 404, "doc_id": "missing-doc"
    }


@pytest.mark.parametrize("bad_id", ["", "../etc/passwd", "a/b", "a\\b", "x" * 129, "a\x00b"])
def test_read_doc_hostile_ids_are_bad_request(ctx, bad_id):
    result = tool_read_doc(ctx, bad_id)
    assert result["ok"] is False
    assert result["status"] == 400


# ----------------------------------------------------------------------
# apply_ops: identity, lock plane, compilation

def test_apply_ops_requires_agent_tagged_client_id(ctx):
    result = tool_apply_ops(ctx, "doc1", "human-1", [{"t": "ins", "at": 0, "text": "X"}])
    assert result["ok"] is False
    assert result["error"] == "agent_client_id_required"
    assert AGENT_PREFIX in result["hint"]


def test_apply_ops_text_edits_compile_and_converge(ctx):
    # base text is "Hello agent" (seeded from the docx)
    result = tool_apply_ops(
        ctx, "doc1", "agent=alfie",
        [
            {"t": "ins", "at": 11, "text": "!"},          # append
            {"t": "ins", "at": 5, "text": ","},           # "Hello, agent!"
            {"t": "del", "at": 0, "end": 6},              # " agent!"
        ],
    )
    assert result["ok"] is True
    assert result["applied_count"] == 3
    assert result["text"] == " agent!"
    # every applied op carries the agent's site -> attributable
    assert all(op["s"] == "agent=alfie" for op in result["applied"])


def test_apply_ops_indices_clamp_into_range(ctx):
    result = tool_apply_ops(ctx, "doc1", "agent=alfie", [{"t": "ins", "at": 10_000, "text": "!"}])
    assert result["ok"] is True
    assert result["text"].endswith("!")


def test_apply_ops_out_of_range_delete_is_a_noop_batch_member(ctx):
    result = tool_apply_ops(ctx, "doc1", "agent=alfie", [{"t": "del", "at": 99, "end": 120}])
    assert result["ok"] is True
    assert result["applied_count"] == 0


def test_apply_ops_on_locked_doc_without_token_is_409_lock_mismatch(ctx):
    ctx.store.set_lock("doc1", "lock-human", "alice")
    result = tool_apply_ops(
        ctx, "doc1", "agent=alfie", [{"t": "ins", "at": 0, "text": "X"}],
        lock_token="wrong-token",
    )
    assert result["ok"] is False
    assert result["error"] == "lock_mismatch"
    assert result["status"] == 409
    assert result["lock"] == "lock-human"  # current token echoed, like WOPI


def test_apply_ops_with_matching_lock_token_applies(ctx):
    ctx.store.set_lock("doc1", "lock-agent", "agent=alfie")
    result = tool_apply_ops(
        ctx, "doc1", "agent=alfie", [{"t": "ins", "at": 11, "text": "!"}],
        lock_token="lock-agent",
    )
    assert result["ok"] is True and result["text"].endswith("!")


def test_apply_ops_unlocked_doc_needs_no_token(ctx):
    result = tool_apply_ops(ctx, "doc1", "agent=alfie", [{"t": "ins", "at": 11, "text": "!"}])
    assert result["ok"] is True


def test_apply_ops_caps_batch_size(ctx):
    ops = [{"t": "ins", "at": 0, "text": "x"}] * (MAX_OPS_PER_CALL + 1)
    result = tool_apply_ops(ctx, "doc1", "agent=alfie", ops)
    assert result["error"] == "too_many_ops"
    assert result["status"] == 413


def test_apply_ops_unknown_doc_is_not_found(ctx):
    result = tool_apply_ops(ctx, "nope", "agent=alfie", [{"t": "ins", "at": 0, "text": "X"}])
    assert result["error"] == "not_found" and result["status"] == 404


def test_apply_ops_empty_or_non_list_ops_rejected(ctx):
    for bad in ([], "not-a-list", None):
        result = tool_apply_ops(ctx, "doc1", "agent=alfie", bad)
        assert result["ok"] is False and result["status"] in (400, 500)
        assert result["status"] == 400  # never an unexpected 500


# ----------------------------------------------------------------------
# get_versions / lock / presence

def test_get_versions_lists_history_newest_first(ctx):
    store = ctx.store
    store.put_content("doc1", _docx_bytes("v2"))
    store.put_content("doc1", _docx_bytes("v3"))
    result = call_tool(ctx, "get_versions", {"doc_id": "doc1"})
    assert result["ok"] is True
    ts_list = [v["ts"] for v in result["versions"]]
    assert ts_list == sorted(ts_list, reverse=True)
    assert len(result["versions"]) >= 3


def test_lock_first_writer_wins_and_mismatch_409(ctx):
    first = tool_lock(ctx, "doc1", "lock", token="agent-lock-1")
    assert first["ok"] is True and first["lock"] == "agent-lock-1"
    second = tool_lock(ctx, "doc1", "lock", token="agent-lock-2")
    assert second["ok"] is False
    assert second["error"] == "lock_mismatch" and second["status"] == 409
    assert second["lock"] == "agent-lock-1"


def test_lock_same_token_is_refresh(ctx):
    tool_lock(ctx, "doc1", "lock", token="tok")
    refreshed = tool_lock(ctx, "doc1", "lock", token="tok")
    assert refreshed["ok"] is True and refreshed.get("refreshed") is True


def test_lock_get_and_unlock(ctx):
    assert tool_lock(ctx, "doc1", "get")["lock"] == ""
    tool_lock(ctx, "doc1", "lock", token="tok")
    assert tool_lock(ctx, "doc1", "get")["locked"] is True
    wrong = tool_lock(ctx, "doc1", "unlock", token="other")
    assert wrong["error"] == "lock_mismatch"
    assert tool_lock(ctx, "doc1", "unlock", token="tok")["ok"] is True
    assert tool_lock(ctx, "doc1", "get")["lock"] == ""


def test_lock_empty_token_rejected(ctx):
    result = tool_lock(ctx, "doc1", "lock", token="")
    assert result["status"] == 400


def test_presence_agent_badge_and_leave(ctx):
    joined = tool_presence(ctx, "doc1", "agent=alfie", user="Alfie", cursor=3)
    assert joined["ok"] is True
    entry = next(c for c in joined["clients"] if c["client"] == "agent=alfie")
    assert entry["agent"] is True
    left = tool_presence(ctx, "doc1", "agent=alfie", leave=True)
    assert all(c["client"] != "agent=alfie" for c in left["clients"])


def test_presence_requires_agent_client_id(ctx):
    result = tool_presence(ctx, "doc1", "browser-1")
    assert result["error"] == "agent_client_id_required"


# ----------------------------------------------------------------------
# compile_text_edit unit coverage

def test_compile_rejects_garbage_edits(ctx):
    crdt = ctx.hub.ensure("c1", "abc").crdt
    for bad in (None, 42, "ins", {}, {"t": "ins"}, {"t": "ins", "at": 0},
                {"t": "ins", "at": "x", "text": "a"}, {"t": "ins", "at": 0, "text": ""},
                {"t": "del", "at": "x"}, {"t": "unknown", "at": 0}):
        assert compile_text_edit(crdt, "agent=x", bad) is None


# ----------------------------------------------------------------------
# call_tool dispatch: hostile calls are typed errors, never exceptions

def test_call_tool_unknown_and_bad_arguments(ctx):
    assert call_tool(ctx, "nope", {})["error"] == "unknown_tool"
    assert call_tool(ctx, "read_doc", "not-an-object")["status"] == 400
    # wrong argument names -> 400, not TypeError leaking out
    assert call_tool(ctx, "read_doc", {"wrong": 1})["status"] == 400


def test_call_tool_disabled_deployment(ctx):
    ctx.agents_enabled = False
    result = call_tool(ctx, "read_doc", {"doc_id": "doc1"})
    assert result["error"] == "agents_disabled" and result["status"] == 403


def test_call_tool_never_raises_on_hostile_arguments(ctx):
    hostile = [
        {"doc_id": ["list"], "ops": {}, "client_id": 1, "at": {}, "text": 2,
         "end": "x", "action": 3, "token": [], "user": {}, "cursor": "x",
         "leave": "x", "ops_tail": "x", "include_content": "x", "arguments": None},
    ]
    for name in TOOL_NAMES:
        for args in hostile:
            result = call_tool(ctx, name, args)
            assert result["ok"] is False
            assert result["status"] in (400, 404, 403, 413, 500)
