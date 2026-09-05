"""Minimal MCP (Model Context Protocol) server over stdio.

Speaks JSON-RPC 2.0, one message per line (newline-delimited), which is the
MCP stdio transport. Implemented by hand against the protocol primitives —
no SDK dependency, ~200 lines, Stoic-small:

    initialize      -> protocolVersion, capabilities, serverInfo
    notifications/* -> acknowledged silently (no id -> no response)
    tools/list      -> the versioned TOOL_CATALOG (model-agnostic schemas)
    tools/call      -> dispatch to ai.tools.call_tool; execution errors are
                       results with isError=true (per MCP), transport errors
                       (unknown method, bad JSON) are JSON-RPC errors
    ping            -> {}

Run standalone (it opens the store from the normal config)::

    uv run python -m src.ai.mcp

or wire it into an agent framework by spawning the process and speaking
JSON-RPC over its stdin/stdout. Roll back by not starting it (or
``DOCSERVER_AGENTS=0``, which makes every tool call return
``agents_disabled``).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

from ..config import Config, load_config
from ..editor.collab import get_hub
from ..lib.store import DocumentStore
from .schemas import TOOL_CATALOG, TOOL_CATALOG_VERSION
from .tools import ToolContext, call_tool

LOG = logging.getLogger("opencloud-docserver.ai.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "world-office-docserver"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes used here.
PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600


class McpServer:
    """Protocol handler — pure with respect to I/O, so the whole surface is
    testable by feeding dicts to :meth:`handle` without spawning a process."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    # -- request handling ------------------------------------------------

    def handle(self, msg: Any) -> dict | None:
        """Handle one decoded JSON-RPC message; return the response dict or
        None for notifications. Malformed messages produce a JSON-RPC error
        response with id=None; the server never crashes on bad input."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._error(None, INVALID_REQUEST, "request must be a JSON-RPC 2.0 object")
        method = msg.get("method")
        req_id = msg.get("id")
        if not isinstance(method, str):
            return self._error(req_id, INVALID_REQUEST, "missing method")
        if req_id is None:
            return None  # notification: acknowledge by silence

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "toolCatalogVersion": TOOL_CATALOG_VERSION,
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOL_CATALOG}}
        if method == "tools/call":
            return {"jsonrpc": "2.0", "id": req_id, "result": self._call(msg.get("params"))}
        return self._error(req_id, METHOD_NOT_FOUND, f"unknown method {method!r}")

    # -- helpers -----------------------------------------------------------

    def _call(self, params: Any) -> dict[str, Any]:
        """tools/call: tool *execution* problems are results with isError
        (per the MCP spec), so the model can read and react to them."""
        if not isinstance(params, dict):
            return _tool_result({"ok": False, "error": "bad_request", "status": 400,
                                 "hint": "params must be an object"}, is_error=True)
        name = params.get("name")
        arguments = params.get("arguments") or {}
        result = call_tool(self.ctx, name if isinstance(name, str) else "", arguments)
        return _tool_result(result, is_error=not result.get("ok", False))

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": message}}

    # -- transport -----------------------------------------------------------

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        """Line-delimited loop: read JSON-RPC lines, write one response line
        per request. A malformed line yields a parse error and the loop
        continues — a hostile client cannot kill the server with garbage.
        EOF terminates."""
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                response = self._error(None, PARSE_ERROR, "invalid JSON")
            else:
                response = self.handle(msg)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()


def _tool_result(result: dict[str, Any], is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result)}],
        "isError": is_error,
    }


def build_context(config: Config | None = None) -> ToolContext:
    """Open the store and hub for standalone (stdio) operation."""
    cfg = config or load_config()
    store = DocumentStore(cfg.database, cfg.content_dir)
    return ToolContext(
        store=store,
        hub=get_hub(),
        agents_enabled=getattr(cfg, "agents_enabled", True),
    )


def main() -> int:
    """stdio entry point: ``uv run python -m src.ai.mcp``."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    server = McpServer(build_context())
    server.serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
