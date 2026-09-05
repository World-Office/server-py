"""Agent permission scope, consent fail-closed, and confused-deputy guards.

Security + fault-injection coverage for the agent surface (TC-E14, stories
E14S2 read-only/edit scopes, E14S3 consent fail-closed, E14S5 kill switch,
E14S7 confused-deputy guard). The only scope control the product currently
exposes is the deployment-wide ``agents_enabled`` gate on
:class:`ToolContext` — a server-side, deny-by-default switch that is
evaluated *before* any tool is dispatched. These tests pin that behaviour:

* a disabled/read-only scope rejects ``apply_ops`` with ``403`` **before**
  any op is applied and leaves the store byte-identical (TC-E14-03);
* fail-closed breadth: with the scope off, **every** tool — read ones
  included — returns ``agents_disabled`` and no privileged call ever reaches
  the store or hub (consent gate, zero egress; TC-E14-05);
* disabling via configuration (env / toml) is honoured and propagates all
  the way through `build_context` to the MCP wire (TC-E14-05);
* a lock token obtained in one delegation context cannot be replayed against
  a document locked by another principal (confused deputy, TC-E14-11);
* a forged ``client_id`` embedded in the op *payload* is ignored — the
  server-side identity wins, so an agent cannot misattribute its edits
  (TC-E14-09);
* revoking the scope mid-session makes the next tool call fail cleanly and
  leaves the document consistent (kill switch, TC-E14-08).

NOTE: the product has no per-agent ``read-only vs edit`` scope yet — the
closest implemented "scope" is the deployment-wide ``agents_enabled`` flag.
These tests pin exactly what exists rather than inventing a scope API.
"""

from __future__ import annotations

import io
import json

import pytest
from docx import Document

from src.ai.mcp import McpServer, build_context
from src.ai.runner import STOP_DONE, AgentRunner
from src.ai.schemas import TOOL_NAMES
from src.ai.tools import ToolContext, call_tool, tool_apply_ops
from src.config import Config, load_config
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


APPLY = {
    "name": "apply_ops",
    "arguments": {
        "doc_id": "doc1",
        "client_id": "agent=alfie",
        "ops": [{"t": "ins", "at": 11, "text": "!"}],
    },
}


# ----------------------------------------------------------------------
# E14S2 / TC-E14-03 — agent permission scope: server-side, deny-by-default

def test_disabled_scope_rejects_apply_ops_before_any_write(ctx):
    """A scope that is off rejects apply_ops with 403 and the stored
    document is byte-identical afterwards (TC-E14-03)."""
    before = ctx.store.get_content("doc1")
    before_meta = ctx.store.get("doc1")
    rev_before = ctx.hub.ensure("doc1", "Hello agent").rev

    ctx.agents_enabled = False
    result = call_tool(ctx, "apply_ops", dict(APPLY["arguments"]))

    assert result["ok"] is False
    assert result["error"] == "agents_disabled"
    assert result["status"] == 403  # scope denied server-side, not in the client
    # no op applied, no revision bumped, store untouched
    assert ctx.store.get_content("doc1") == before
    assert ctx.store.get("doc1") == before_meta
    assert ctx.hub.ensure("doc1", "Hello agent").rev == rev_before


def test_disabled_scope_fails_closed_for_every_tool_no_egress(ctx):
    """Consent fail-closed: with the scope off, every tool — including
    read-only ones — is denied, so not even document bytes can leave the
    server (TC-E14-03/E14S3)."""
    ctx.agents_enabled = False
    for name in TOOL_NAMES:
        args = {"doc_id": "doc1"}
        if name == "apply_ops":
            args = dict(APPLY["arguments"])
        elif name in ("presence",):
            args["client_id"] = "agent=alfie"
        elif name == "lock":
            args["action"] = "get"
        result = call_tool(ctx, name, args)
        assert result["ok"] is False, name
        assert result["error"] == "agents_disabled", name
        assert result["status"] == 403, name
        # fail-closed: no payload (e.g. read_doc text/base64) is returned
        assert "text" not in result and "content_base64" not in result, name


def test_scope_gate_precedes_all_privileged_work(monkeypatch):
    """Fault injection: when the scope is off, the permission check runs
    before dispatch — the store and hub are never even touched (zero
    privileged calls), so no side channel survives the gate."""
    calls = {"n": 0}

    class Tripwire:
        def __getattr__(self, _name):
            calls["n"] += 1
            raise AssertionError("privileged component reached past the scope gate")

    ctx = ToolContext(store=Tripwire(), hub=Tripwire(), agents_enabled=False)
    # hostile input as well — denial must be independent of argument shape
    hostile = [None, {}, {"doc_id": []}, {"ops": "garbage"}, {"client_id": 7}]
    for name in TOOL_NAMES:
        for args in hostile:
            result = call_tool(ctx, name, args)
            assert result["error"] == "agents_disabled" and result["status"] == 403
            assert result["ok"] is False
    assert calls["n"] == 0  # the gate truly precedes every privileged call


# ----------------------------------------------------------------------
# E14S3 / TC-E14-05 — consent fail-closed wiring (config -> MCP wire)

def _write_config(tmp_path, toml_body: str) -> str:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(toml_body)
    return str(cfg_path)


def test_config_disable_is_fail_closed_and_propagates_to_mcp(tmp_path, monkeypatch):
    """Disabling the surface via env or toml makes load_config fail-closed,
    build_context propagates it into the ToolContext, and MCP tools/call
    surfaces it as a typed isError result — the model can never reach the
    document when consent is withheld."""
    # env override: 0 / false / no all mean disabled
    for raw in ("0", "false", "no"):
        monkeypatch.setenv("DOCSERVER_AGENTS", raw)
        cfg = load_config(_write_config(tmp_path, ""))
        assert cfg.agents_enabled is False
    # toml [ai] enabled=false is honoured too
    monkeypatch.delenv("DOCSERVER_AGENTS", raising=False)
    cfg = load_config(_write_config(tmp_path, "[ai]\nenabled = false\n"))
    assert cfg.agents_enabled is False

    # propagates through build_context to the MCP wire
    cfg2 = Config(
        database=str(tmp_path / "b.db"),
        content_dir=str(tmp_path / "bc"),
        agents_enabled=False,
    )
    ctx = build_context(cfg2)
    assert ctx.agents_enabled is False

    server = McpServer(ctx)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "apply_ops", "arguments": dict(APPLY["arguments"])}}
    result = server.handle(msg)["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "agents_disabled" and payload["status"] == 403


# ----------------------------------------------------------------------
# E14S7 / TC-E14-11 — confused deputy: no cross-context privilege transfer

def test_confused_deputy_lock_token_does_not_transfer_across_documents(ctx):
    """An agent legitimately holding the lock on document A must not be able
    to reuse that privilege against document B. B is locked by another
    principal; the agent's foreign token is rejected with the WOPI 409
    contract and B's bytes stay untouched (TC-E14-11)."""
    # agent takes a legitimate lock on A in its own delegation context
    ctx.store.init("docA", "a.docx")
    ctx.store.put_content("docA", _docx_bytes("A"))
    agent_grant_on_a = tool_apply_ops(ctx, "docA", "agent=alfie",
                                      [{"t": "ins", "at": 1, "text": "!"}])
    assert agent_grant_on_a["ok"] is True

    # document B is locked by a *different* principal (a human co-author)
    ctx.store.init("docB", "b.docx")
    ctx.store.put_content("docB", _docx_bytes("B"))
    ctx.store.set_lock("docB", "lock-human-alice", "alice")
    before_b = ctx.store.get_content("docB")

    # deputy tries to replay its docA privilege (same token shape) on docB
    result = tool_apply_ops(
        ctx, "docB", "agent=alfie",
        [{"t": "ins", "at": 1, "text": "!"}],
        lock_token="agent-grant",  # token from the agent's own A context
    )
    assert result["ok"] is False
    assert result["error"] == "lock_mismatch"
    assert result["status"] == 409
    assert result["lock"] == "lock-human-alice"  # B's real token echoed
    assert ctx.store.get_content("docB") == before_b  # no escalation happened


def test_forged_client_id_in_payload_is_ignored_server_side(ctx):
    """Server-side identity wins (TC-E14-09): a client_id smuggled inside
    the op payload (`s`/`site` fields) is ignored — attribution records the
    server-provided agent identity, so an agent cannot blame edits on
    another agent (or on a human)."""
    forged = [
        {"t": "ins", "at": 0, "text": "X", "s": "agent=evil", "site": "human-9"},
        {"t": "del", "at": 0, "end": 1, "s": "agent=evil"},
    ]
    result = tool_apply_ops(ctx, "doc1", "agent=alfie", forged)
    assert result["ok"] is True
    assert len(result["applied"]) == 2
    # every op in the hub's log is attributed to agent=alfie, never the forgery
    ops = ctx.hub.ops_since("doc1", 0)
    agent_ops = [op for op in ops if op.get("s", "").startswith("agent=")]
    assert agent_ops, "records the agent site, a forged site never appears"
    assert all(op["s"] == "agent=alfie" for op in agent_ops)
    assert all("agent=evil" not in str(op) and "human-9" not in str(op) for op in ops)


# ----------------------------------------------------------------------
# E14S5 / TC-E14-08 — kill switch mid-session (fault injection)

def test_kill_switch_mid_session_blocks_next_call_document_consistent(ctx):
    """Revoking the scope mid-session (fault injection): the op that got
    through before the revoke stays applied; the next tool call fails with
    403 and the loop stops cleanly — the document stays consistent, nothing
    half-applied, no exception escapes the runner (TC-E14-08)."""

    class FlippingModel:
        """First turn edits; second turn revokes the scope, then stops."""

        def __init__(self):
            self.turns = 0

        def __call__(self, _messages):
            self.turns += 1
            if self.turns == 1:
                return [dict(APPLY)]
            if self.turns == 2:
                ctx.agents_enabled = False  # kill switch pulled mid-session
                return [dict(APPLY)]
            return []

    report = AgentRunner(FlippingModel()).run(ctx, "doc1", "agent=alfie", "shout")

    assert report.stopped_reason == STOP_DONE
    assert report.ops_applied == 1  # only the pre-revoke op landed
    assert report.text == "Hello agent!"  # document consistent
    # the post-revoke call is visible in the transcript as a typed 403
    failed = [t for t in report.transcript if not t["result"].get("ok")]
    assert len(failed) == 1
    assert failed[0]["result"]["error"] == "agents_disabled"
    assert failed[0]["result"]["status"] == 403
    # the failed call did not bump the revision or touch the store
    assert ctx.hub.ensure("doc1", "Hello agent!").rev == report.rev
