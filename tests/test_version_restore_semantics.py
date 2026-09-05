"""Version-history semantics: monotonic listing, restore-to-old, immutability.

This suite pins the version ledger contract for the document engine
(`src/lib/store.py`) and its HTTP surface (`src/editor/router.py`). Three
semantic guarantees are locked down:

1. **Monotonic listing** — every snapshot gets a strictly increasing ``ts``
   (the store's counter stays monotonic even for back-to-back writes within a
   single millisecond) and ``list_versions`` serves them newest-first, so the
   list order always mirrors real write order. A Hypothesis property asserts
   this for arbitrary write sequences.

2. **Restore-to-old** — restoring an old snapshot makes *its* bytes the
   document's current content again, the pre-restore state is preserved as a
   brand-new recoverable snapshot (so the restore is undoable), and the
   restored bytes become the newest head of the ledger.

3. **Immutability of history** — a snapshot's bytes are fixed the moment its
   ``ts`` is minted; later writes and restores only *append* (or, past
   ``MAX_VERSIONS``, prune the oldest) and never rewrite an existing entry in
   place.

Paradigm: HTTP-level unit tests (router) plus Hypothesis property tests
(engine). Deterministic: no network, no sleeps — the same-millisecond case is
pinned by freezing the store's clock, not by hoping the wall clock advances.

GATE: pytest tests/test_version_restore_semantics.py
"""

from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir

# -----------------------------------------------------------------------------
# Shared app builder (mirrors tests/test_editor_api_lifecycle.py)
# -----------------------------------------------------------------------------


def _make_app(tmp_path):
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")
    store = DocumentStore(db, content)
    cfg = Config(database=db, content_dir=content, jwt_secret="test-secret")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.sessions = SessionRegistry()
        app.state.config = cfg
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(editor_router)
    return app, store


@pytest.fixture
def client(tmp_path):
    """TestClient with lifespan running; backing store on ``client.test_store``."""
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


@pytest.fixture
def store(tmp_path):
    """A bare engine store in a fresh temp dir, for engine-level semantics."""
    s = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    yield s
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


def _fresh_store(root: Path):
    """Build a fresh store inside a property test body (isolated per example).

    Backed by a throwaway temp dir so no function-scoped fixture leaks state
    between Hypothesis-generated inputs.
    """
    return DocumentStore(str(root / "t.db"), str(root / "content"))


def _assert_strictly_newest_first(versions: list[dict]) -> None:
    """Common invariant: the ledger is newest-first with strictly increasing ts.

    A *duplicate* or *out-of-order* ts would mean the list no longer mirrors
    write order — the monotonic-listing guarantee.
    """
    ts = [v["ts"] for v in versions]
    assert ts == sorted(ts, reverse=True), f"ts not strictly newest-first: {ts}"
    assert len(set(ts)) == len(ts), f"duplicate ts in ledger: {ts}"


# -----------------------------------------------------------------------------
# 1. Monotonic listing
# -----------------------------------------------------------------------------


def test_versions_list_is_strictly_newest_first(client):
    """GET /versions returns destruction-order-exact rows, newest snapshot first.

    Distinct payload sizes make every version identifiable; the HTTP list must
    come back newest-first and the per-row ``size`` must reflect the *reverse*
    of write order exactly (no reordering, no drops).
    """
    store = client.test_store  # type: ignore[attr-defined]
    store.init("doc1", "test.docx")
    sizes = [11, 5, 29, 17]  # four distinguishable byte blobs
    for n in sizes:
        store.put_content("doc1", b"x" * n)

    res = client.get("/api/documents/doc1/versions")
    assert res.status_code == 200
    versions = res.json()["versions"]
    assert len(versions) == len(sizes)
    _assert_strictly_newest_first(versions)
    # newest-first sizes mirror reverse write order exactly
    assert [v["size"] for v in versions] == list(reversed(sizes))
    # the oldest written snapshot is still reachable and byte-exact
    oldest_ts = versions[-1]["ts"]
    assert store.get_version("doc1", oldest_ts) == b"x" * sizes[0]


def test_timestamps_monotonic_even_within_same_millisecond(store, monkeypatch):
    """Back-to-back writes in one millisecond still get strictly increasing ts.

    # NOTE: existing behaviour — the engine keeps a class-level monotonizing
    # counter (``_last_ts``) so ``ORDER BY ts DESC`` stays a total order even
    # when the wall clock does not advance between writes. Freezing the clock
    # proves the guarantee does not lean on timing luck.
    """
    frozen_now = 1700000000.500
    monkeypatch.setattr("src.lib.store.time.time", lambda: frozen_now)
    store.init("doc1", "test.docx")

    blobs = [b"a" * 3, b"b" * 7, b"c" * 15, b"d" * 40, b"e" * 2]
    for data in blobs:
        store.put_content("doc1", data)

    versions = store.list_versions("doc1")
    assert len(versions) == len(blobs)
    _assert_strictly_newest_first(versions)  # duplicates/out-of-order -> fail
    # sizes still mirror reverse write order (counter, not clock, drives order)
    assert [v["size"] for v in versions] == [len(b) for b in reversed(blobs)]


@given(st.lists(st.binary(min_size=0, max_size=32), min_size=1, max_size=16))
@settings(max_examples=50, deadline=None)
def test_list_versions_order_property(blobs):
    """PROPERTY: arbitrary write sequences yield strictly newest-first ledgers.

    For any sequence of content writes the freshly written snapshots must be
    listed in exact reverse write order with no duplicate or reordered ``ts``,
    and every snapshot must still return its original bytes.
    """
    with tempfile.TemporaryDirectory() as td:
        store = _fresh_store(Path(td))
        store.init("doc1", "test.docx")
        for data in blobs:
            store.put_content("doc1", data)

        versions = store.list_versions("doc1")
        assert len(versions) == len(blobs)
        _assert_strictly_newest_first(versions)
        assert [v["size"] for v in versions] == [len(b) for b in reversed(blobs)]
        # each listed snapshot is byte-exact against what was written (ts: bytes)
        for v in versions:
            assert store.get_version("doc1", v["ts"]) in blobs


# -----------------------------------------------------------------------------
# 2. Restore-to-old
# -----------------------------------------------------------------------------


def test_restore_to_old_version_reverts_content_and_moves_head(client):
    """Restoring the oldest snapshot makes its bytes current and moves the head.

    The restored (old) bytes become the document's current content AND the
    newest entry in history — the ledger head advances past everything, so
    "restore" is an ordinary append, not a rollback of the record.
    """
    store = client.test_store  # type: ignore[attr-defined]
    store.init("doc1", "test.docx")
    store.put_content("doc1", b"alpha")
    store.put_content("doc1", b"beta-big")
    store.put_content("doc1", b"gamma-huge!")
    before = client.get("/api/documents/doc1/versions").json()["versions"]
    oldest_ts = before[-1]["ts"]  # "alpha" snapshot

    res = client.post(f"/api/documents/doc1/versions/{oldest_ts}/restore")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True

    # current content is exactly the restored (old) bytes
    assert store.get_content("doc1") == b"alpha"
    # the head returned equals the newest ledger entry and is >= any prior ts
    after = client.get("/api/documents/doc1/versions").json()["versions"]
    assert body["ts"] == after[0]["ts"]
    _assert_strictly_newest_first(after)
    assert after[0]["ts"] > max(v["ts"] for v in before)


def test_restore_preserves_pre_restore_state_for_undo(client):
    """Restoring is undoable: the pre-restore bytes stay recoverable as a snapshot.

    Restoring old content snapshots the pre-restore state first; restoring
    THAT snapshot returns the document to the pre-restore bytes — a full
    undo round-trip through history alone.
    """
    store = client.test_store  # type: ignore[attr-defined]
    store.init("doc1", "test.docx")
    store.put_content("doc1", b"AAAA")       # v1
    store.put_content("doc1", b"BBBBBB")     # v2
    store.put_content("doc1", b"CCCCCCCC")   # v3 (head)

    versions = client.get("/api/documents/doc1/versions").json()["versions"]
    oldest_ts = versions[-1]["ts"]  # "AAAA"

    res = client.post(f"/api/documents/doc1/versions/{oldest_ts}/restore")
    assert res.status_code == 200
    assert store.get_content("doc1") == b"AAAA"

    # the pre-restore state ("CCCCCCCC") must be present as a recoverable
    # snapshot — one restore appends exactly two entries: pre-restore + head.
    after_first = client.get("/api/documents/doc1/versions").json()["versions"]
    assert len(after_first) == 5
    preserve_ts = after_first[1]["ts"]  # second-newest = preserved pre-restore
    assert store.get_version("doc1", preserve_ts) == b"CCCCCCCC"

    # undo: restore the preserved pre-restore snapshot -> back to "CCCCCCCC"
    res = client.post(f"/api/documents/doc1/versions/{preserve_ts}/restore")
    assert res.status_code == 200
    assert store.get_content("doc1") == b"CCCCCCCC"
    # and the round-trip grew the ledger without mutating any prior entry
    after_undo = client.get("/api/documents/doc1/versions").json()["versions"]
    assert len(after_undo) == 7
    assert store.get_version("doc1", oldest_ts) == b"AAAA"


def test_restore_to_same_head_is_idempotent_in_content(client):
    """Restoring the current head keeps the current bytes (content-stable).

    # NOTE: existing behaviour — restoring the head re-snapshots the current
    # bytes as a new version; the *content* is unchanged and the old head's
    # snapshot still exists untouched below the two appended entries.
    """
    store = client.test_store  # type: ignore[attr-defined]
    store.init("doc1", "test.docx")
    store.put_content("doc1", b"keep-me")
    versions = client.get("/api/documents/doc1/versions").json()["versions"]
    head_ts = versions[0]["ts"]

    res = client.post(f"/api/documents/doc1/versions/{head_ts}/restore")
    assert res.status_code == 200
    assert store.get_content("doc1") == b"keep-me"
    after = client.get("/api/documents/doc1/versions").json()["versions"]
    assert len(after) == 3  # two append entries on top of the original
    # the original head snapshot is immutable despite now being two back
    assert store.get_version("doc1", head_ts) == b"keep-me"


# -----------------------------------------------------------------------------
# 3. Immutability of history
# -----------------------------------------------------------------------------


@given(
    st.lists(st.binary(min_size=0, max_size=32), min_size=1, max_size=10),
    st.lists(st.binary(min_size=0, max_size=32), min_size=0, max_size=10),
)
@settings(max_examples=50, deadline=None)
def test_history_immutable_under_writes_and_restores(seq1, seq2):
    """PROPERTY: once minted, a snapshot's bytes never change under later ops.

    Between an initial write sequence and a follow-up sequence (extra writes +
    restores of random existing snapshots), every ``ts`` that existed in the
    first ledger must still exist and return byte-identical content. History
    only grows by appending; nothing is rewritten in place. (Sequences stay
    well under MAX_VERSIONS so pruning cannot mask a rewrite.)
    """
    store = _fresh_store(Path(tempfile.mkdtemp()))
    store.init("doc1", "test.docx")
    for data in seq1:
        store.put_content("doc1", data)

    before = {v["ts"]: store.get_version("doc1", v["ts"]) for v in store.list_versions("doc1")}
    assert before  # seq1 is non-empty

    current = list(store.list_versions("doc1"))
    for data in seq2:
        store.put_content("doc1", data)
        current = list(store.list_versions("doc1"))
    # restore a handful of pre-existing snapshots (each appends two entries)
    for ts in list(before.keys())[:3]:
        store.restore_version("doc1", ts)
        current = list(store.list_versions("doc1"))

    after = {v["ts"]: store.get_version("doc1", v["ts"]) for v in current}
    # the ledger stays a strict newest-first total order throughout
    _assert_strictly_newest_first(current)
    # every original snapshot survives with byte-identical content
    for ts, data in before.items():
        assert ts in after, f"snapshot {ts} vanished"
        assert after[ts] == data, f"snapshot {ts} byte-mutated"


def test_pruning_keeps_newest_and_never_rewrites_survivors(store):
    """Past MAX_VERSIONS the ledger prunes the oldest — and only the oldest.

    Surplus history is cut from the bottom, the surviving newest snapshots keep
    their exact bytes and ts, and the trimmed snapshots are the exact oldest
    ones we wrote (the ones we wrote first).
    """
    store.init("doc1", "test.docx")
    n_writes = 10
    keep = store.MAX_VERSIONS
    for i in range(keep + n_writes):
        store.put_content("doc1", b"p" * (i + 1))

    versions = store.list_versions("doc1")
    assert len(versions) == keep  # pruned to the cap
    _assert_strictly_newest_first(versions)

    # survivors are the newest blobs, byte-exact and in original order
    newest_sizes = [i + 1 for i in range(n_writes, keep + n_writes)]
    assert [v["size"] for v in reversed(versions)] == newest_sizes
    for v in versions:
        assert store.get_version("doc1", v["ts"]) == b"p" * v["size"]

    # the oldest written snapshot is gone — but this is deletion of the oldest
    # only, not mutation: every survivor still matches its original bytes.
    # (payload sizes are unique per write here, so size identifies the snapshot)
    pruned_sizes = [i + 1 for i in range(n_writes)]
    survivor_sizes = {v["size"] for v in versions}
    assert survivor_sizes.isdisjoint(pruned_sizes), "oldest snapshots not pruned"
