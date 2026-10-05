"""MCP stdio server: protocol handling and hostile-input resilience.

The handler is pure (dict in, dict out), so the whole protocol surface is
exercised without spawning a process; :meth:`McpServer.serve` is tested
end-to-end over StringIO, including the garbage-line case.
"""

from __future__ import annotations

import io
import json

import pytest
from docx import Document

from src.ai.mcp import McpServer, build_context
from src.ai.schemas import TOOL_CATALOG
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str = "MCP doc") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def server(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "mcp.docx")
    store.put_content("doc1", _docx_bytes())
    ctx = ToolContext(store=store, hub=CollabHub())
    yield McpServer(ctx)
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _rpc(server, method, params=None, req_id=1):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def test_initialize_handshake(server):
    result = _rpc(server, "initialize")["result"]
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"]
    assert result["toolCatalogVersion"]


def test_tools_list_returns_the_catalog(server):
    tools = _rpc(server, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == [t["name"] for t in TOOL_CATALOG]


def test_tools_call_roundtrip(server):
    result = _rpc(server, "tools/call", {
        "name": "read_doc", "arguments": {"doc_id": "doc1"}
    })["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True and payload["name"] == "mcp.docx"


def test_tools_call_tool_failure_is_isError_result_not_protocol_error(server):
    # per MCP spec: execution errors are results the model can read
    result = _rpc(server, "tools/call", {
        "name": "read_doc", "arguments": {"doc_id": "missing"}
    })["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "not_found"


def test_tools_call_apply_ops_through_mcp(server):
    result = _rpc(server, "tools/call", {
        "name": "apply_ops",
        "arguments": {
            "doc_id": "doc1", "client_id": "agent=mcp",
            "ops": [{"t": "ins", "at": 7, "text": "!"}],
        },
    })["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["text"].endswith("!")


def test_unknown_method_is_jsonrpc_error(server):
    resp = _rpc(server, "resources/list")
    assert resp["error"]["code"] == -32601


def test_malformed_messages_never_crash(server):
    assert server.handle(None)["error"]["code"] == -32600
    assert server.handle("hello")["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 2})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "1.0", "id": 3, "method": "ping"})["error"]["code"] == -32600


def test_notifications_acknowledged_silently(server):
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_serve_survives_garbage_lines_and_answers_valid_ones(server):
    stdin = io.StringIO(
        "this is not json\n"
        "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/list"}) + "\n"
    )
    out = io.StringIO()
    server.serve(stdin, out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(lines) == 3
    assert lines[0]["error"]["code"] == -32700  # parse error, loop continued
    assert lines[1]["result"] == {}
    assert len(lines[2]["result"]["tools"]) == 7


def test_build_context_standalone(tmp_path):
    """build_context wires store + hub for standalone stdio operation."""
    from src.config import Config

    cfg = Config(database=str(tmp_path / "b.db"), content_dir=str(tmp_path / "bc"))
    ctx = build_context(cfg)
    assert ctx.store is not None and ctx.hub is not None
    assert ctx.agents_enabled is True
