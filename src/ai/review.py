"""AI review: the op-stream diff between human and agent work.

Every agent op is attributable (site ``agent=<name>``) and invertible from
the CRDT alone — inserts invert to deletes of their item ids, deletes invert
to a re-insert of the tombstoned characters at their original alive index.
That makes accept/reject *per op* a pure op-stream operation: no new data
model, no parallel history (spec: agent-collab-client "revertible and
reviewable").

Rejections are applied as fresh ops by the rejecting client (``reviewer``),
so they themselves are visible, attributable and undoable in the normal
history.
"""

from __future__ import annotations

from typing import Any

from ..editor.collab import CollabHub, TextCRDT
from . import AGENT_PREFIX


def _is_agent_op(op: dict) -> bool:
    return str(op.get("s", "")).startswith(AGENT_PREFIX)


def summarize(op: dict) -> str:
    """One-line human-readable description of an op (review list row)."""
    if op.get("t") == "insert":
        chars = str(op.get("chars", ""))
        shown = chars if len(chars) <= 20 else chars[:17] + "..."
        shown = shown.replace("\n", "\\n")
        return f'insert "{shown}"'
    ids = op.get("ids") or []
    return f"delete {len(ids)} char(s)"


def agent_ops(hub: CollabHub, doc_id: str, since_rev: int = 0) -> dict[str, Any]:
    """The reviewable agent portion of a document's op stream.

    Returns the ops with their hub revisions (log index + 1), the agent
    name, a summary, and — for delete ops — the text that the delete
    removed (needed to preview what a rejection would restore).
    """
    state = hub.ensure(doc_id)
    crdt = state.crdt
    out: list[dict[str, Any]] = []
    for rev, op in enumerate(state.log, start=1):
        if rev <= max(0, since_rev) or not isinstance(op, dict) or not _is_agent_op(op):
            continue
        entry: dict[str, Any] = {
            "rev": rev,
            "agent": str(op.get("s", "")),
            "type": op.get("t"),
            "summary": summarize(op),
        }
        if op.get("t") == "insert":
            entry["text"] = op.get("chars", "")
        else:
            entry["removed_text"] = _deleted_text(crdt, op.get("ids") or [])
        out.append(entry)
    return {
        "doc_id": doc_id,
        "rev": state.rev,
        "text": crdt.to_string(),
        "ops": out,
    }


def _deleted_text(crdt: TextCRDT, ids: list) -> str:
    chars: list[str] = []
    for pair in ids:
        try:
            site, seq = pair[0], pair[1]
        except (TypeError, IndexError):
            continue
        item = crdt.items.get((site, seq))
        if item is not None:
            chars.append(item.char)
    return "".join(chars)


def reject_agent_ops(hub: CollabHub, doc_id: str, revs: list[int]) -> dict[str, Any]:
    """Undo the agent ops identified by *revs* (applied newest-first).

    For each targeted revision the inverse op is computed against the
    *current* CRDT state and applied immediately, so consecutive rejections
    stay consistent:

    * an agent **insert** inverts to a delete of exactly the item ids it
      created (ids are stable, so this works no matter what happened since);
    * an agent **delete** inverts to a re-insert of the tombstoned
      characters at the alive index where the run used to sit (a fresh
      ``ins`` text edit compiled through the same machinery agent edits use).

    Returns the rejection report: per-rev outcome plus the resulting text.
    """
    from .tools import compile_text_edit  # local import avoids an import cycle

    state = hub.ensure(doc_id)
    crdt = state.crdt
    results: list[dict[str, Any]] = []
    applied_any = False

    for rev in sorted(set(int(r) for r in revs), reverse=True):
        if not 1 <= rev <= len(state.log):
            results.append({"rev": rev, "ok": False, "error": "unknown_rev"})
            continue
        op = state.log[rev - 1]
        if not isinstance(op, dict) or not _is_agent_op(op):
            results.append({"rev": rev, "ok": False, "error": "not_an_agent_op"})
            continue

        if op.get("t") == "insert":
            ids = [[op.get("s"), (op.get("b") or 0) + i] for i in range(op.get("n") or 0)]
            inverse = {"t": "delete", "s": "reviewer", "ids": ids}
        else:
            ids = op.get("ids") or []
            text = _deleted_text(crdt, ids)
            if not text:
                results.append({"rev": rev, "ok": False, "error": "nothing_to_restore"})
                continue
            at = _first_id(crdt, ids)
            inverse = compile_text_edit(crdt, "reviewer", {"t": "ins", "at": at, "text": text})
            if inverse is None:
                results.append({"rev": rev, "ok": False, "error": "compile_failed"})
                continue

        reply = hub.apply_ops(doc_id, "reviewer", [inverse])
        applied = reply.get("applied") or []
        results.append({
            "rev": rev, "ok": bool(applied),
            "error": None if applied else "already_reverted",
        })
        applied_any = applied_any or bool(applied)

    return {
        "doc_id": doc_id,
        "rejected": results,
        "text": state.crdt.to_string(),
        "rev": state.rev,
        "applied_any": applied_any,
    }


def _first_id(crdt: TextCRDT, ids: list) -> int:
    """Alive index of the first (tombstoned) id of a deleted run."""
    try:
        return crdt.insert_index_at((ids[0][0], ids[0][1]))
    except (TypeError, IndexError):
        return crdt.alive_count


__all__ = ["agent_ops", "reject_agent_ops"]
