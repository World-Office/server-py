"""Real-time collaborative editing — character-level CRDT + collaboration hub.

This module implements the server side of real-time collaborative editing
for the docserver: a lightweight **tombstone RGA** (Replicated Growable
Array) sequence CRDT together with a per-document collaboration hub that
orders operations, deduplicates re-delivery, serves late-join replays and
pushes live updates to connected clients over a Server-Sent-Events stream.

Design
------
* Every character is an *item* with a globally unique id ``(site, seq)``
  where ``seq`` is a Lamport counter owned by the site that created it.
  Inserts record the id of the item they were inserted after (`origin`);
  the document root anchor is ``("", 0)``.
* Removed characters are *tombstoned* — kept in the item table, marked
  dead — so the insert graph stays intact and any two concurrent edits
  commute.
* Ordering rule: children that share an origin are sorted by
  ``(seq, site)`` ascending. The exact tie-break is arbitrary; what matters
  is that *every* replica applies the same deterministic rule, so any set
  of operations applied in **any** order converges to the same text.
* Deletes may arrive before the inserts they target (lossy or reordered
  delivery). They are parked in a pending table and flushed the moment the
  item integrates.

Wire format (JSON object, one per operation)::

    insert:
        {"t": "insert", "s": "site-A", "b": 3, "n": 4, "chars": "text",
         "originSite": "", "originSeq": 0}
    delete:
        {"t": "delete", "s": "site-A", "ids": [["site-B", 2], ["site-B", 3]]}

An insert op creates ``chars[i]`` as item ``(site, b + i)``; a delete op
tombstones each of the listed item ids. Ops are idempotent and commute, so
a client that replays the hub's op log from a given revision (late join)
always converges with the other editors.

The hub assigns every applied op a global, monotonic *revision* (its index
in the hub's op log). Clients track the last revision they applied and poll
``GET .../collab/ops?since=<rev>`` — or subscribe to the SSE stream
``GET .../collab/stream`` — to stay in sync.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

# Root anchor: origin of the very first characters of a document.
ROOT = ("", 0)
# Site id used by the hub when it seeds the CRDT from the stored document,
# so a fresh collaboration state reflects the document as it exists today.
BASE_SITE = "__base__"

T_INSERT = "insert"
T_DELETE = "delete"


@dataclass(eq=False)
class Item:
    """One character in the sequence CRDT (alive or tombstoned)."""

    site: str
    seq: int
    origin_site: str
    origin_seq: int
    char: str
    alive: bool = True

    @property
    def id(self) -> tuple[str, int]:
        return (self.site, self.seq)

    @property
    def origin(self) -> tuple[str, int]:
        return (self.origin_site, self.origin_seq)


def op_key(op: dict) -> tuple | None:
    """Canonical, hashable identity of an operation (used for dedup).

    An insert is uniquely identified by the first item id it creates
    ``(site, b)`` and its length; a delete by its target id list.

    Malformed ops (wrong types, e.g. ``ids`` as a string) yield ``None``
    so the hub skips them instead of unpacking garbage.
    """
    if not isinstance(op, dict):
        return None
    if op.get("t") == T_INSERT:
        return ("i", op.get("s"), op.get("b"), op.get("n", 0))
    if op.get("t") == T_DELETE:
        ids = op.get("ids") or []
        if not isinstance(ids, list):
            return None
        try:
            return ("d", op.get("s"), tuple((s, q) for s, q in ids))
        except (TypeError, ValueError):
            return None
    return None


class TextCRDT:
    """Tombstone RGA sequence CRDT over characters.

    Not thread-safe by itself — the hub serializes access. Local edits are
    generated with :meth:`local_insert` / :meth:`local_delete` (which also
    integrate them); remote edits arrive as op dicts through
    :meth:`integrate`.
    """

    def __init__(self, site_id: str, initial_text: str = "") -> None:
        self.site_id = site_id
        self.lamport: dict[str, int] = {}
        self.items: dict[tuple[str, int], Item] = {}
        self._children: dict[tuple[str, int], list[tuple[str, int]]] = {ROOT: []}
        self._pending_deletes: set[tuple[str, int]] = set()
        self._order_cache: list[tuple[str, int]] | None = None
        self._text_cache: str | None = None
        self.seed_op: dict | None = None
        if initial_text:
            self._seed(initial_text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        seq = self.lamport.get(self.site_id, 0) + 1
        self.lamport[self.site_id] = seq
        return seq

    def _alloc_seq_range(self, count: int) -> int:
        """Allocate ``count`` consecutive seqs (items (site, seq+i)); the
        Lamport clock must advance past the whole run so later edits never
        collide with ids already claimed by this site."""
        seq = self.lamport.get(self.site_id, 0) + 1
        self.lamport[self.site_id] = seq + max(count, 1) - 1
        return seq

    def _touch(self, site: str, seq: int) -> None:
        self.lamport[site] = max(self.lamport.get(site, 0), seq)

    def _add_item(self, item: Item) -> None:
        """Insert an item and attach it under its origin, keeping siblings
        in the deterministic order: descending by ``(seq, site)`` so a newer
        edit sits closest to its anchor (standard RGA sibling rule)."""
        self.items[item.id] = item
        self._children.setdefault(item.origin, []).append(item.id)
        self._children[item.origin].sort(
            key=lambda iid: (self.items[iid].seq, self.items[iid].site), reverse=True
        )
        self._order_cache = None
        self._text_cache = None
        if item.id in self._pending_deletes:
            # A delete for this item arrived before the insert: apply it now.
            item.alive = False
            self._pending_deletes.discard(item.id)
            self._text_cache = None

    def _seed(self, text: str) -> None:
        """Insert ``text`` as one base op by the hub's site.

        Characters are **chained** (each char's origin is the previous
        char), the standard RGA encoding of existing content, so sibling
        tie-breaks never perturb the original order. The op is kept in
        :attr:`seed_op` so the hub can log it for late joiners.
        """
        seq = self._alloc_seq_range(len(text))
        prev = ROOT
        for i, ch in enumerate(text):
            self._add_item(Item(self.site_id, seq + i, prev[0], prev[1], ch))
            prev = (self.site_id, seq + i)
        self.seed_op = {
            "t": T_INSERT,
            "s": self.site_id,
            "b": seq,
            "n": len(text),
            "chars": text,
            "originSite": ROOT[0],
            "originSeq": ROOT[1],
        }

    # ------------------------------------------------------------------
    # Local edit generation (client side / tests)
    # ------------------------------------------------------------------

    def local_insert(self, index: int, text: str) -> dict:
        """Insert ``text`` at char *index* on this replica.

        Returns the insert op (already integrated locally) so the caller can
        ship it to peers. ``index`` counts alive characters; out-of-range
        indices are clamped.
        """
        if not text:
            return {
                "t": T_INSERT, "s": self.site_id, "b": 0, "n": 0,
                "chars": "", "originSite": ROOT[0], "originSeq": ROOT[1],
            }
        alive = self._alive_ids()
        if index < 0:
            index = 0
        if index > len(alive):
            index = len(alive)
        origin = alive[index - 1] if index > 0 else ROOT
        seq = self._alloc_seq_range(len(text))
        prev = origin
        for i, ch in enumerate(text):
            self._add_item(Item(self.site_id, seq + i, prev[0], prev[1], ch))
            prev = (self.site_id, seq + i)
        return {
            "t": T_INSERT,
            "s": self.site_id,
            "b": seq,
            "n": len(text),
            "chars": text,
            "originSite": origin[0],
            "originSeq": origin[1],
        }

    def local_delete(self, start: int, end: int | None = None) -> dict:
        """Delete alive chars in ``[start, end)`` (end None → single char).

        Returns the delete op (already integrated locally) listing every
        tombstoned item id, so peers can remove exactly the same characters.
        """
        if end is None:
            end = start + 1
        alive = self._alive_ids()
        start = max(0, min(start, len(alive)))
        end = max(start, min(end, len(alive)))
        targets = alive[start:end]
        for iid in targets:
            self.items[iid].alive = False
            self._text_cache = None
        return {
            "t": T_DELETE,
            "s": self.site_id,
            "ids": [[s, q] for (s, q) in targets],
        }

    # ------------------------------------------------------------------
    # Remote integration
    # ------------------------------------------------------------------

    def integrate(self, op: dict) -> bool:
        """Apply one operation. Idempotent — duplicate or stale deletes are
        no-ops. Returns True if it changed this replica's state.

        Concurrency semantics rely on the CRDT, not on delivery order:
        * an insert whose origin has not arrived yet still attaches under
          the (future) origin and becomes visible once the origin arrives;
        * a delete for an unseen item is parked and applied when the item
          integrates.

        Ops arrive over the wire from untrusted clients, so every field is
        type-validated up front: a malformed op is rejected whole (returns
        False) instead of crashing the replica or being half-applied.
        """
        if not isinstance(op, dict) or op.get("t") not in (T_INSERT, T_DELETE):
            return False
        site = op.get("s", "")
        if not isinstance(site, str) or not site:
            return False
        changed = False

        if op["t"] == T_INSERT:
            start = op.get("b", 0)
            if not isinstance(start, int):
                return False
            origin_site = op.get("originSite", "")
            origin_seq = op.get("originSeq", 0)
            if not isinstance(origin_site, str) or not isinstance(origin_seq, int):
                return False
            origin = (origin_site, origin_seq)
            chars = op.get("chars", "") or ""
            if not isinstance(chars, str):
                return False
            self._touch(site, start + len(chars) - 1)
            # reconstruct the same chain the generating site built: the
            # first char anchors at `origin`, each following char anchors at
            # the previous one. This keeps the run in order regardless of
            # the sibling tie-break and matches local_insert exactly.
            prev = origin
            for i, ch in enumerate(chars):
                iid = (site, start + i)
                if iid in self.items:
                    prev = iid  # already applied — stay chained for later chars
                    continue
                self._add_item(Item(site, start + i, prev[0], prev[1], ch))
                prev = iid
                changed = True
            return changed

        # delete
        ids = op.get("ids") or []
        if not isinstance(ids, list):
            return False
        # validate the whole id list up front — never partially apply a
        # malformed op (a string here would otherwise unpack char-by-char)
        for sid, seq in ids:
            if not isinstance(sid, str) or not isinstance(seq, int):
                return False
        max_seq = 0
        for sid, seq in ids:
            max_seq = max(max_seq, seq)
            iid = (sid, seq)
            item = self.items.get(iid)
            if item is None:
                self._pending_deletes.add(iid)
            elif item.alive:
                item.alive = False
                self._text_cache = None
                changed = True
        self._touch(site, max_seq)
        return changed

    # ------------------------------------------------------------------
    # Materialization
    # ------------------------------------------------------------------

    def _ordered_ids(self) -> list[tuple[str, int]]:
        if self._order_cache is not None:
            return self._order_cache
        ids: list[tuple[str, int]] = []

        def walk(origin: tuple[str, int]) -> None:
            for iid in self._children.get(origin, ()):
                ids.append(iid)
                walk(iid)

        walk(ROOT)
        self._order_cache = ids
        return ids

    def _alive_ids(self) -> list[tuple[str, int]]:
        return [iid for iid in self._ordered_ids() if self.items[iid].alive]

    def alive_ids(self) -> list[tuple[str, int]]:
        """Public view of the alive item ids (visible-char order).

        Used by the agent tool surface to compile index-based text edits
        into CRDT wire ops without reaching into private state.
        """
        return self._alive_ids()

    def insert_index_at(self, iid: tuple[str, int]) -> int:
        """The alive index at which *iid* sits or would be re-inserted:
        the number of alive items preceding it in document order. Works for
        tombstoned ids too — that is how a rejected agent delete knows where
        to put the removed characters back."""
        pos = 0
        for cur in self._ordered_ids():
            if cur == iid:
                return pos
            if self.items[cur].alive:
                pos += 1
        return pos

    def to_string(self) -> str:
        """The materialized (visible) text of this replica."""
        if self._text_cache is not None:
            return self._text_cache
        out: list[str] = []

        def walk(origin: tuple[str, int]) -> None:
            for iid in self._children.get(origin, ()):
                item = self.items[iid]
                if item.alive:
                    out.append(item.char)
                walk(iid)

        walk(ROOT)
        self._text_cache = "".join(out)
        return self._text_cache

    def char_at(self, index: int) -> str | None:
        """The visible character at *index*, or None when out of range."""
        alive = self._alive_ids()
        if not 0 <= index < len(alive):
            return None
        return self.items[alive[index]].char

    @property
    def alive_count(self) -> int:
        return len(self._alive_ids())


# ----------------------------------------------------------------------
# Collaboration hub
# ----------------------------------------------------------------------


class CollabDocState:
    """Per-document collaboration state: CRDT + global op log + presence."""

    def __init__(self, doc_id: str, initial_text: str = "", site_id: str = BASE_SITE) -> None:
        self.doc_id = doc_id
        self.crdt = TextCRDT(site_id=site_id, initial_text=initial_text)
        self.log: list[dict] = []
        self.rev: int = 0
        self.seen: set = set()
        self.presence: dict[str, dict] = {}
        if self.crdt.seed_op is not None:
            self.log.append(self.crdt.seed_op)
            self.seen.add(op_key(self.crdt.seed_op))
            self.rev = 1

    def snapshot(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "rev": self.rev,
            "text": self.crdt.to_string(),
            "ops": list(self.log),
            "clients": [dict(c) for c in self.presence.values()],
        }


class CollabHub:
    """In-memory hub ordering operations across editors of a document.

    All handles for one document are the source of truth for the op log:
    * :meth:`apply_ops` integrates new operations (deduplicated), assigns
      each a global revision and fans the event out to SSE subscribers;
    * :meth:`ops_since` replays everything after a revision (late join);
    * :meth:`resync` rebases the CRDT onto authoritative text (e.g. after
      a full save) while keeping live subscribers connected.
    """

    def __init__(self) -> None:
        self._docs: dict[str, CollabDocState] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    # -- document lookup ------------------------------------------------

    def ensure(self, doc_id: str, initial_text: str = "") -> CollabDocState:
        """Return the collaboration state for a document, seeding it from
        ``initial_text`` the first time it is touched."""
        doc = self._docs.get(doc_id)
        if doc is None:
            doc = CollabDocState(doc_id, initial_text=initial_text)
            self._docs[doc_id] = doc
        return doc

    def rev(self, doc_id: str) -> int:
        return self.ensure(doc_id).rev

    def state(self, doc_id: str) -> dict:
        return self.ensure(doc_id).snapshot()

    def ops_since(self, doc_id: str, rev: int) -> list[dict]:
        """All ops the hub applied after revision *rev* (0 → everything)."""
        doc = self.ensure(doc_id)
        rev = max(0, rev)
        return list(doc.log[rev:])

    # -- applying operations -------------------------------------------

    def apply_ops(
        self,
        doc_id: str,
        client_id: str,
        ops: list[dict],
        base_rev: int | None = None,
    ) -> dict:
        """Integrate a batch of client ops.

        Ops already known to the hub are skipped (idempotent under
        re-delivery); the reply carries the hub's new revision, the ops
        that were newly applied, plus everything the client is still
        missing since ``base_rev`` (so a single round-trip heals any gap).
        """
        doc = self.ensure(doc_id)
        applied: list[dict] = []
        for op in ops:
            if not isinstance(op, dict):
                continue
            key = op_key(op)
            if key is None or key in doc.seen:
                continue
            if doc.crdt.integrate(op):
                doc.seen.add(key)
                doc.log.append(op)
                doc.rev += 1
                applied.append(op)
        if doc.presence:
            # bump the author's presence so peer lists reflect activity
            if client_id in doc.presence:
                doc.presence[client_id]["updated"] = time.time()
        if applied:
            # real-time push: every live SSE subscriber sees the new ops.
            # Carry the full converged text so a thin browser client can apply
            # the change directly without a follow-up /collab/state fetch.
            self._emit(
                doc_id,
                {
                    "type": "ops",
                    "doc_id": doc_id,
                    "ops": applied,
                    "text": doc.crdt.to_string(),
                },
            )
        return {
            "doc_id": doc_id,
            "rev": doc.rev,
            "applied": applied,
            "ops": self.ops_since(doc_id, base_rev if base_rev is not None else 0),
            "text": doc.crdt.to_string(),
        }

    def sync_text(self, doc_id: str, client_id: str, text: str) -> dict:
        """Merge a client's full-text snapshot into the CRDT.

        The browser never runs the CRDT: it ships its plain-text content and
        the server diffs it against the current CRDT text (common prefix /
        suffix), applies the resulting insert/delete ops on the document's own
        CRDT, and fans them out to every live subscriber. Two clients editing
        the same document both converge because all snapshots are serialized
        through the single CRDT.
        """
        doc = self.ensure(doc_id)
        cur = doc.crdt.to_string()
        text = str(text)
        if cur == text:
            return doc.snapshot()
        # common prefix
        i = 0
        max_i = min(len(cur), len(text))
        while i < max_i and cur[i] == text[i]:
            i += 1
        # common suffix (after the divergent middle)
        j = 0
        while (
            j < (len(cur) - i)
            and j < (len(text) - i)
            and cur[len(cur) - 1 - j] == text[len(text) - 1 - j]
        ):
            j += 1
        del_start = i
        del_end = len(cur) - j
        insert_text = text[i : len(text) - j]
        # local_insert/local_delete mutate the CRDT and return the op dicts;
        # we then record them as applied (bump rev, log, fan out) ourselves
        # rather than re-feeding them to apply_ops, which would treat already
        # integrated ops as duplicates and skip the revision bump + emit.
        ops: list[dict] = []
        if del_end > del_start:
            ops.append(doc.crdt.local_delete(del_start, del_end))
        if insert_text:
            ops.append(doc.crdt.local_insert(del_start, insert_text))
        ops = [o for o in ops if (o.get("chars") or o.get("ids"))]
        if ops:
            doc.rev += len(ops)
            for o in ops:
                doc.seen.add(op_key(o))
                doc.log.append(o)
            self._emit(
                doc_id,
                {"type": "ops", "doc_id": doc_id, "ops": ops, "text": doc.crdt.to_string()},
            )
        return doc.snapshot()

    def resync(self, doc_id: str, text: str) -> dict:
        """Rebase the document state onto authoritative text.

        Used after a full save so the collaboration layer and the stored
        document do not drift apart. Live subscribers stay connected and
        receive a ``resync`` event.
        """
        old_subs = self._subscribers.get(doc_id)
        doc = CollabDocState(doc_id, initial_text=text)
        self._docs[doc_id] = doc
        if old_subs:
            self._subscribers[doc_id] = old_subs
        self._emit(doc_id, {"type": "resync", "doc_id": doc_id, "rev": doc.rev, "text": text})
        return doc.snapshot()

    # -- presence -------------------------------------------------------

    def set_presence(self, doc_id: str, client_id: str, user: str = "", cursor=None) -> list[dict]:
        """Announce a client. Cursor ``None`` removes the client (leave)."""
        doc = self.ensure(doc_id)
        if not client_id:
            return self.clients(doc_id)
        if cursor is None:
            doc.presence.pop(client_id, None)
        else:
            # Agents announce themselves as "agent=<name>" clients; the badge
            # makes them distinguishable from human editors in every UI that
            # renders the presence list.
            doc.presence[client_id] = {
                "client": client_id,
                "user": user or client_id,
                "cursor": cursor,
                "updated": time.time(),
                "agent": client_id.startswith("agent="),
            }
        clients = self.clients(doc_id)
        self._emit(doc_id, {"type": "presence", "doc_id": doc_id, "clients": clients})
        return clients

    def clients(self, doc_id: str) -> list[dict]:
        return [dict(c) for c in self.ensure(doc_id).presence.values()]

    # -- SSE fan-out ----------------------------------------------------

    def subscribe(self, doc_id: str) -> asyncio.Queue:
        """Register a subscriber queue for a document's live events."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(doc_id, set()).add(queue)
        return queue

    def unsubscribe(self, doc_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(doc_id)
        if subs:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(doc_id, None)

    def _emit(self, doc_id: str, event: dict) -> None:
        subs = self._subscribers.get(doc_id)
        if not subs:
            return
        payload = json.dumps(event, default=str)
        for queue in list(subs):
            try:
                queue.put_nowait(payload)
            except Exception:
                # A stuck/closed subscriber must never break the hub.
                subs.discard(queue)

    # -- tests ----------------------------------------------------------

    def reset(self) -> None:
        """Drop all documents and subscribers (test isolation)."""
        self._docs.clear()
        self._subscribers.clear()


_HUB: CollabHub | None = None


def get_hub() -> CollabHub:
    """Module-level hub singleton shared by all requests/apps."""
    global _HUB
    if _HUB is None:
        _HUB = CollabHub()
    return _HUB


def reset_hub() -> None:
    """Clear the shared hub (used by the test suite)."""
    global _HUB
    if _HUB is not None:
        _HUB.reset()
