"""The five agent tools, mapped onto DocumentStore and CollabHub.

Every tool returns a uniform JSON envelope::

    {"ok": true,  ...payload}
    {"ok": false, "error": "<code>", "status": <http-equivalent>, ...detail}

Tools never raise: any unexpected exception is converted to an ``internal``
error result so a hostile/malformed call can at worst fail one call, never
the hub or the server. HTTP-equivalent statuses mirror the existing WOPI
contracts (404 not found, 409 lock mismatch, 400 bad request, 413 too large).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from ..editor.collab import ROOT, T_INSERT, CollabHub, TextCRDT, op_key
from ..editor.converter import docx_to_html
from ..editor.odt_converter import odt_to_html
from ..lib.store import DocumentStore
from ..wopi.protocol import invalid_doc_id
from . import AGENT_PREFIX, is_agent_client
from .schemas import TOOL_NAMES

# Lock-mismatch status, mirroring wopi.protocol.HTTP_LOCK_MISMATCH.
HTTP_LOCK_MISMATCH = 409

#: Hard cap on ops per apply_ops call (agent-loop runaway protection).
MAX_OPS_PER_CALL = 200


@dataclass
class ToolContext:
    """Everything a tool needs: the store and the collaboration hub."""

    store: DocumentStore
    hub: CollabHub
    agents_enabled: bool = True


# ----------------------------------------------------------------------
# Envelope helpers
# ----------------------------------------------------------------------


def ok_result(**payload: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, **payload}


def err_result(error: str, status: int, **detail: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "status": status, **detail}


def _not_found(doc_id: str) -> dict[str, Any]:
    return err_result("not_found", 404, doc_id=doc_id)


# ----------------------------------------------------------------------
# Baseline text (mirrors editor.router._collab_base_text, store-only)
# ----------------------------------------------------------------------


def _base_text(store: DocumentStore, doc_id: str, name: str) -> str:
    """Best-effort plain text for a document's collaboration baseline.

    The stored bytes are converted to plain text exactly like the editor
    does, so an agent's insert indices mean the same thing to the CRDT as
    a browser editor's. Returns "" when there is nothing to seed from.
    """
    from ..editor.router import _html_to_text  # local import: avoids cycles

    data = store.get_content(doc_id)
    if not data:
        return ""
    try:
        if name.lower().endswith(".odt"):
            html = odt_to_html(data)
        else:
            html = docx_to_html(data)
    except Exception:
        return ""
    return _html_to_text(html)


# ----------------------------------------------------------------------
# Text-edit compilation: agent-friendly edits -> CRDT wire ops
# ----------------------------------------------------------------------


def compile_text_edit(crdt: TextCRDT, site: str, edit: Any) -> dict | None:
    """Compile one agent text edit into a CRDT wire op.

    ``{"t": "ins", "at": i, "text": "..."}`` inserts at visible char index
    *i*; ``{"t": "del", "at": i, "end": j}`` tombstones visible chars in
    ``[i, j)`` (end omitted → single char). Indices clamp into range;
    no-op edits compile to ``None`` and are skipped.

    The op's site is the agent's ``client_id``, so attribution travels with
    the op through the log. Sequence numbers are allocated from the
    document's **global** Lamport clock (max over all sites), mirroring the
    full-text-sync path: a freshly applied insert sorts closest to its
    anchor, so the agent's index semantics are exact at apply time — the
    same guarantee a browser editor's edits get.
    """
    if not isinstance(edit, dict):
        return None
    kind = edit.get("t")
    alive = crdt.alive_ids()
    if kind == "ins":
        at = edit.get("at", 0)
        text = edit.get("text", "")
        if not isinstance(at, int) or not isinstance(text, str) or not text:
            return None
        at = max(0, min(at, len(alive)))
        origin = alive[at - 1] if at > 0 else ROOT
        seq = max(crdt.lamport.values(), default=0) + 1
        return {
            "t": T_INSERT,
            "s": site,
            "b": seq,
            "n": len(text),
            "chars": text,
            "originSite": origin[0],
            "originSeq": origin[1],
        }
    if kind == "del":
        at = edit.get("at", 0)
        end = edit.get("end", None)
        if not isinstance(at, int):
            return None
        if end is None or not isinstance(end, int):
            end = at + 1
        at = max(0, min(at, len(alive)))
        end = max(at, min(end, len(alive)))
        targets = alive[at:end]
        if not targets:
            return None
        return {"t": "delete", "s": site, "ids": [[s, q] for (s, q) in targets]}
    return None


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------


def tool_read_doc(
    ctx: ToolContext,
    doc_id: str,
    ops_tail: int = 50,
    include_content: bool = False,
) -> dict[str, Any]:
    """Document metadata + current collaborative text + op-log tail."""
    if not isinstance(doc_id, str) or invalid_doc_id(doc_id):
        return err_result("bad_request", 400, doc_id=doc_id)
    doc = ctx.store.get(doc_id)
    if doc is None:
        return _not_found(doc_id)
    state = ctx.hub.ensure(doc_id, _base_text(ctx.store, doc_id, doc["name"])).snapshot()
    tail = max(0, min(int(ops_tail) if isinstance(ops_tail, int) else 50, 500))
    payload: dict[str, Any] = {
        "doc_id": doc_id,
        "name": doc["name"],
        "size": doc["size"],
        "lock": ctx.store.get_lock(doc_id),
        "rev": state["rev"],
        "text": state["text"],
        "ops": state["ops"][-tail:],
        "versions": len(ctx.store.list_versions(doc_id)),
    }
    if include_content:
        raw = ctx.store.get_content(doc_id) or b""
        payload["content_base64"] = base64.b64encode(raw).decode("ascii")
    return ok_result(**payload)


def tool_apply_ops(
    ctx: ToolContext,
    doc_id: str,
    client_id: str,
    ops: list[dict],
    base_rev: int | None = None,
    lock_token: str = "",
) -> dict[str, Any]:
    """Apply agent edits through the collaboration op pipeline.

    Contract (identical to the human path):
    * unknown/invalid doc → 404 not_found / 400 bad_request;
    * locked document without the matching token → 409 lock_mismatch with
      the current token echoed (the same contract PutFile returns);
    * malformed ops are skipped by the hub's guards — the hub stays up and
      the document stays consistent;
    * applied ops enter the op log, bump the revision, and fan out to
      every live subscriber.
    """
    if not isinstance(doc_id, str) or invalid_doc_id(doc_id):
        return err_result("bad_request", 400, doc_id=doc_id)
    if ctx.store.get(doc_id) is None:
        return _not_found(doc_id)
    if not is_agent_client(client_id):
        return err_result(
            "agent_client_id_required", 400,
            hint=f"client_id must start with {AGENT_PREFIX!r} so edits are attributable",
        )
    if not isinstance(ops, list) or not ops:
        return err_result("bad_request", 400, hint="ops must be a non-empty list")
    if len(ops) > MAX_OPS_PER_CALL:
        return err_result(
            "too_many_ops", 413, limit=MAX_OPS_PER_CALL,
            hint="split the batch; agents are budgeted per call",
        )
    # WOPI lock plane: a locked document requires the matching token.
    current_lock = ctx.store.get_lock(doc_id)
    if current_lock and lock_token != current_lock:
        return err_result(
            "lock_mismatch", HTTP_LOCK_MISMATCH,
            lock=current_lock,
            hint="supply the current lock token in lock_token",
        )

    hub_reply = _apply_through_hub(ctx, doc_id, client_id, ops, base_rev)
    return ok_result(
        doc_id=doc_id,
        client_id=client_id,
        rev=hub_reply["rev"],
        applied=hub_reply["applied"],
        applied_count=len(hub_reply["applied"]),
        text=hub_reply["text"],
    )


def _apply_through_hub(
    ctx: ToolContext,
    doc_id: str,
    client_id: str,
    ops: list[dict],
    base_rev: int | None,
) -> dict[str, Any]:
    """Integrate ops one by one (text edits compile against the live CRDT,
    so each edit sees the text state after the previous one) and return the
    hub-shaped reply for the whole batch."""
    state = ctx.hub.ensure(doc_id, _base_text(ctx.store, doc_id, _doc_name(ctx, doc_id)))
    applied: list[dict] = []
    for op in ops:
        if isinstance(op, dict) and op.get("t") in ("ins", "del"):
            compiled = compile_text_edit(state.crdt, client_id, op)
            wire = [compiled] if compiled is not None else []
        else:
            wire = [op]
        reply = ctx.hub.apply_ops(doc_id, client_id, wire, base_rev=None)
        applied.extend(reply["applied"])
    return {
        "rev": state.rev,
        "applied": applied,
        "text": state.crdt.to_string(),
    }


def _doc_name(ctx: ToolContext, doc_id: str) -> str:
    doc = ctx.store.get(doc_id)
    return doc["name"] if doc else ""


def tool_get_versions(ctx: ToolContext, doc_id: str) -> dict[str, Any]:
    """Version history metadata, newest first."""
    if not isinstance(doc_id, str) or invalid_doc_id(doc_id):
        return err_result("bad_request", 400, doc_id=doc_id)
    if ctx.store.get(doc_id) is None:
        return _not_found(doc_id)
    return ok_result(doc_id=doc_id, versions=ctx.store.list_versions(doc_id))


def tool_lock(
    ctx: ToolContext,
    doc_id: str,
    action: str,
    token: str = "",
    user: str = "",
) -> dict[str, Any]:
    """WOPI lock plane for agents: lock/unlock/refresh/get.

    Semantics mirror the WOPI endpoints exactly: tokens must be non-empty,
    locks are first-writer-wins, a same-token lock is a refresh, and a
    conflicting call returns the 409 lock-mismatch contract with the
    current token echoed.
    """
    if not isinstance(doc_id, str) or invalid_doc_id(doc_id):
        return err_result("bad_request", 400, doc_id=doc_id)
    if ctx.store.get(doc_id) is None:
        return _not_found(doc_id)
    if action not in ("lock", "unlock", "refresh", "get"):
        return err_result("bad_request", 400, hint=f"unknown action {action!r}")
    current = ctx.store.get_lock(doc_id)

    if action == "get":
        return ok_result(doc_id=doc_id, action="get", lock=current, locked=bool(current))

    if not isinstance(token, str) or not token:
        return err_result("bad_request", 400, hint="lock token must be non-empty")

    if action == "lock":
        if current:
            if token == current:
                return ok_result(doc_id=doc_id, action="lock", lock=current, refreshed=True)
            return err_result(
                "lock_mismatch", HTTP_LOCK_MISMATCH, lock=current,
                hint="document is locked by another client (first-writer-wins)",
            )
        ctx.store.set_lock(doc_id, token, user or client_of(token))
        return ok_result(doc_id=doc_id, action="lock", lock=token)

    if action == "unlock":
        if current and current != token:
            return err_result("lock_mismatch", HTTP_LOCK_MISMATCH, lock=current)
        ctx.store.release_lock(doc_id)
        return ok_result(doc_id=doc_id, action="unlock", lock="")

    # refresh
    if current and token != current:
        return err_result("lock_mismatch", HTTP_LOCK_MISMATCH, lock=current)
    ctx.store.set_lock(doc_id, token, user or client_of(token))
    return ok_result(doc_id=doc_id, action="refresh", lock=token)


def client_of(token: str) -> str:
    """Display name for a lock taken by a bare token."""
    return token


def tool_presence(
    ctx: ToolContext,
    doc_id: str,
    client_id: str,
    user: str = "",
    cursor: int | None = 0,
    leave: bool = False,
) -> dict[str, Any]:
    """Announce/update/leave the presence list. Agents get an agent badge."""
    if not isinstance(doc_id, str) or invalid_doc_id(doc_id):
        return err_result("bad_request", 400, doc_id=doc_id)
    if not is_agent_client(client_id):
        return err_result(
            "agent_client_id_required", 400,
            hint=f"client_id must start with {AGENT_PREFIX!r}",
        )
    if leave:
        cursor = None
    elif not isinstance(cursor, int):
        cursor = 0
    clients = ctx.hub.set_presence(
        doc_id, client_id, user=user or client_id, cursor=cursor
    )
    return ok_result(
        doc_id=doc_id, client_id=client_id, left=bool(leave), clients=clients
    )


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------

_TOOLS = {
    "read_doc": tool_read_doc,
    "apply_ops": tool_apply_ops,
    "get_versions": tool_get_versions,
    "lock": tool_lock,
    "presence": tool_presence,
}

assert set(_TOOLS) == set(TOOL_NAMES), "tool registry and catalog drifted apart"


def call_tool(ctx: ToolContext, name: str, arguments: dict | None) -> dict[str, Any]:
    """Dispatch one tool call. Never raises; unknown tools and bad argument
    payloads come back as typed error results (MCP maps these to
    ``isError`` results, not protocol errors)."""
    if not ctx.agents_enabled:
        return err_result("agents_disabled", 403, hint="agent tools are disabled on this deployment")
    tool = _TOOLS.get(name)
    if tool is None:
        return err_result("unknown_tool", 404, tool=name, known=list(TOOL_NAMES))
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return err_result("bad_request", 400, hint="arguments must be an object")
    try:
        return tool(ctx, **arguments)
    except TypeError as exc:
        # wrong argument names / types — a client bug, not a server error
        return err_result("bad_request", 400, hint=str(exc))
    except Exception as exc:  # noqa: BLE001 — the boundary: report, never crash
        return err_result("internal", 500, detail=f"{type(exc).__name__}: {exc}")


__all__ = [
    "MAX_OPS_PER_CALL",
    "ToolContext",
    "call_tool",
    "compile_text_edit",
    "err_result",
    "ok_result",
    "op_key",
    "tool_apply_ops",
    "tool_get_versions",
    "tool_lock",
    "tool_presence",
    "tool_read_doc",
]
