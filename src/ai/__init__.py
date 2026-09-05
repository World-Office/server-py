"""Agentic AI surface for opencloud-docserver.

Agents are **collaboration clients, not a new primitive**: they read and edit
documents through the same store, WOPI lock plane, and CRDT op pipeline that
human editors use. This package adds:

* :mod:`ai.tools`     — the five agent tools (read_doc, apply_ops,
  get_versions, lock, presence) mapped onto DocumentStore + CollabHub.
* :mod:`ai.schemas`   — model-agnostic, versioned tool schemas (plain JSON
  Schema; no vendor-specific fields).
* :mod:`ai.mcp`       — a minimal MCP (Model Context Protocol) server over
  stdio (JSON-RPC 2.0, newline-delimited) exposing the tool catalog.
* :mod:`ai.runner`    — a thin, model-agnostic ``AgentRunner`` that translates
  model tool calls into tool invocations (and therefore into ops).

Safety model (unchanged from human editing):

* every write goes through ``CollabHub.apply_ops`` → dedup, guards, op log;
* agent ops carry the agent's site id (``agent=<name>``) so they are
  attributable in the op stream and presence list;
* a document locked by another client rejects agent writes with the same
  ``409`` lock-mismatch contract a human client gets;
* malformed/corrupt input is rejected as a typed result — the hub never
  crashes and the document stays consistent.

Roll back by disabling this package (`DOCSERVER_AGENTS=0`): human editing is
unaffected.
"""

from __future__ import annotations

AGENT_PREFIX = "agent="


def is_agent_client(client_id: str) -> bool:
    """True when *client_id* identifies an agent (``agent=<name>``)."""
    return isinstance(client_id, str) and client_id.startswith(AGENT_PREFIX)
