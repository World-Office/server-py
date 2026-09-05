"""Tests for the real-time collaboration layer (character CRDT + hub + API).

Covers, per the collaboration contract:
  * CRDT correctness — convergence under concurrent edits, commutativity,
    idempotency (duplicate/lost/reordered delivery), delete-before-insert,
    insert-before-parent, deterministic ordering, Unicode handling.
  * Hub behaviour — global revisions, dedup, late-join replay (CO-4),
    presence (CO-3), SSE fan-out, resync after a save.
  * HTTP API — state / ops / presence / resync / stream endpoints.
"""

from __future__ import annotations

import io
import json
import random
import socket
import threading
import time
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.collab import (
    BASE_SITE,
    TextCRDT,
    get_hub,
    op_key,
    reset_hub,
)
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir

UNICODE_SAMPLES = ["héllo wörld", "日本語のテキスト", "emoji 🎉🚀 test", "a\u0301 combining"]

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_app(tmp_path) -> tuple[FastAPI, DocumentStore]:
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
    reset_hub()  # the hub is a module singleton — isolate per test
    app, store = _make_app(tmp_path)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(str(tmp_path / "t.db"))
    wipe_dir(str(tmp_path / "content"))
    reset_hub()


@pytest.fixture
def server_client(tmp_path):
    """A client over a real ASGI server. TestClient deadlocks on SSE streams
    (external-thread + internal event loop), so realtime tests need a real
    uvicorn server. ``test_store`` is still attached for direct seeding."""
    reset_hub()
    app, store = _make_app(tmp_path)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    app_port = sock.getsockname()[1]
    sock.close()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=app_port, log_level="error")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", app_port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.05)
    c = httpx.Client(base_url=f"http://127.0.0.1:{app_port}")
    c.test_store = store  # type: ignore[attr-defined]
    yield c
    c.close()
    srv.should_exit = True
    time.sleep(0.2)
    wipe_db(str(tmp_path / "t.db"))
    wipe_dir(str(tmp_path / "content"))
    reset_hub()


def _docx_bytes(text: str = "Hello world") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _seed_store(client, doc_id: str = "doc.docx", text: str = "Hello world") -> None:
    """Register a document with real DOCX content in the store."""
    store = client.test_store
    store.init(doc_id, doc_id)
    store.put_content(doc_id, _docx_bytes(text))


def _collab_base(client, doc_id: str = "doc.docx", text: str = "Hello world") -> str:
    """Seed a document and return the HTML base text the hub seeds with
    (what the state endpoint reports before any client edits)."""
    _seed_store(client, doc_id, text)
    data = client.get(f"/api/documents/{doc_id}/collab/state").json()
    assert data["rev"] == 1
    return data["text"]


def _peer(site: str, base: TextCRDT) -> TextCRDT:
    """A replica that shares ``base``'s items (integrates its seed op), so
    cross-replica inserts/deletes reference ids both sides understand."""
    replica = TextCRDT(site)
    if base.seed_op is not None:
        replica.integrate(base.seed_op)
    return replica


def _editor(client, doc_id: str, site: str) -> TextCRDT:
    """A client replica built the way a real editor joins: by integrating the
    hub's full op log (seed included), so its local insert origins reference
    item ids the hub actually has."""
    ops = client.get(f"/api/documents/{doc_id}/collab/state").json()["ops"]
    replica = TextCRDT(site)
    for op in ops:
        replica.integrate(op)
    return replica


# ----------------------------------------------------------------------
# TextCRDT — local edits
# ----------------------------------------------------------------------


def test_seed_materializes_initial_text():
    crdt = TextCRDT("hub", initial_text="Hello world")
    assert crdt.to_string() == "Hello world"
    assert crdt.alive_count == 11


def test_local_insert_generates_and_integrates_op():
    crdt = TextCRDT("site-A", initial_text="")
    op = crdt.local_insert(0, "abc")
    assert op["t"] == "insert"
    assert op["chars"] == "abc"
    assert op["originSite"] == ""
    assert crdt.to_string() == "abc"
    # shipping the same op to a fresh replica reproduces the text
    other = TextCRDT("site-B")
    other.integrate(op)
    assert other.to_string() == "abc"


def test_local_insert_middle():
    crdt = TextCRDT("site-A", initial_text="Hello world")
    op = crdt.local_insert(5, " beautiful")
    assert op["chars"] == " beautiful"
    assert crdt.to_string() == "Hello beautiful world"


def test_local_delete_span():
    crdt = TextCRDT("site-A", initial_text="Hello world")
    op = crdt.local_delete(5, 11)
    assert crdt.to_string() == "Hello"
    # delete op carries the exact ids of the removed characters
    assert op["t"] == "delete"
    assert len(op["ids"]) == 6
    other = _peer("site-B", crdt)
    other.integrate(op)
    assert other.to_string() == "Hello"


def test_insert_at_clamped_indices():
    crdt = TextCRDT("site-A", initial_text="abcd")
    crdt.local_insert(-5, "X")
    assert crdt.to_string() == "Xabcd"
    crdt.local_insert(99, "Z")
    assert crdt.to_string() == "XabcdZ"


def test_unicode_counts_chars_not_bytes():
    text = "äöü✨"
    crdt = TextCRDT("site-A")
    op = crdt.local_insert(0, text)
    assert crdt.to_string() == text
    assert crdt.alive_count == 4  # four characters, more than four bytes
    assert op["n"] == 4


def test_unicode_delete_span():
    crdt = TextCRDT("site-A", initial_text="a✨b")
    crdt.local_delete(1, 2)  # delete the emoji as one character
    assert crdt.to_string() == "ab"


# ----------------------------------------------------------------------
# TextCRDT — concurrency and convergence
# ----------------------------------------------------------------------


def test_idempotent_replay_duplicate_ops():
    a = TextCRDT("A")
    op_alpha = a.local_insert(0, "alpha")
    op_beta = a.local_insert(5, " beta")
    # deliver ops to a peer, with the last one delivered twice
    nodup = TextCRDT("B")
    nodup.integrate(op_alpha)
    nodup.integrate(op_beta)
    nodup.integrate(op_beta)
    assert nodup.to_string() == a.to_string()
    # re-delivering a delete is a no-op as well
    delete = a.local_delete(0, 5)
    nodup.integrate(delete)
    nodup.integrate(delete)
    assert nodup.to_string() == a.to_string()


def test_concurrent_inserts_same_position_converge():
    crdt = TextCRDT("hub", initial_text="||")
    op_a = crdt.local_insert(1, "AAA")
    op_b = crdt.local_insert(1, "BBB")  # both anchored after the first '|'

    replica_a = _peer("R1", crdt)
    replica_b = _peer("R2", crdt)
    replica_a.integrate(op_a)
    replica_a.integrate(op_b)
    replica_b.integrate(op_b)
    replica_b.integrate(op_a)

    # deterministic sibling order => both replicas agree
    assert replica_a.to_string() == replica_b.to_string()
    # and BOTH inserts survive (no lost update)
    text = replica_a.to_string()
    assert "AAA" in text and "BBB" in text
    assert text.startswith("|") and text.endswith("|")


def test_concurrent_insert_and_delete_converge():
    base = "Hello world"
    ins_crdt = TextCRDT("hub", initial_text=base)
    insert_op = ins_crdt.local_insert(6, "brave ")  # "Hello brave world"
    del_crdt = TextCRDT("hub", initial_text=base)
    delete_op = del_crdt.local_delete(6, 11)  # delete "world"

    r1 = _peer("R1", ins_crdt)
    r1.integrate(insert_op)
    r1.integrate(delete_op)
    r2 = _peer("R2", del_crdt)
    r2.integrate(delete_op)
    r2.integrate(insert_op)

    assert r1.to_string() == r2.to_string()
    assert "brave" in r1.to_string()
    assert "world" not in r1.to_string()


def test_commutativity_any_subset_order():
    base = "the quick brown fox"
    gen = TextCRDT("hub", initial_text=base)
    ops = [
        gen.local_insert(4, "very "),
        gen.local_insert(16, "lazy "),
        gen.local_delete(16, 19),  # "fox"
        gen.local_insert(0, "A "),
    ]
    reference = gen.to_string()
    for _ in range(50):
        ordered = random.sample(ops, len(ops))
        replica = _peer("R", gen)
        for op in ordered:
            replica.integrate(op)
        assert replica.to_string() == reference


def test_delete_before_insert_is_parked_then_applied():
    """A delete delivered before the insert it targets must still win."""
    base = "0123456789"
    gen = TextCRDT("hub", initial_text=base)
    insert_op = gen.local_insert(5, "XX")      # "01234XX56789"
    delete_op = gen.local_delete(5, 7)          # deletes the "XX"
    assert gen.to_string() == "0123456789"

    replica = _peer("R", gen)
    replica.integrate(delete_op)                # targets unseen items
    assert replica.to_string() == base          # parked, nothing removed yet
    replica.integrate(insert_op)                # items arrive -> delete flushes
    assert replica.to_string() == "0123456789"


def test_insert_before_parent_arrives():
    """An insert whose origin item has not arrived yet still converges."""
    a = TextCRDT("A")
    anchor = a.local_insert(0, "bc")            # ids (A,1),(A,2)
    mid = a.local_insert(1, "X")                # origin = (A,1)
    assert a.to_string() == "bXc"

    replica = TextCRDT("R")
    replica.integrate(mid)                      # origin (A,1) absent
    replica.integrate(anchor)
    assert replica.to_string() == "bXc"


def test_deterministic_sibling_order_locked():
    crdt = TextCRDT("hub", initial_text="|")
    op_b = crdt.local_insert(1, "B")
    op_a = crdt.local_insert(1, "A")
    # concurrent siblings with the same origin sort by (seq, site); B's
    # insert got the higher seq, so B sits closer to the origin.
    replica = _peer("R", crdt)
    replica.integrate(op_b)
    replica.integrate(op_a)
    assert replica.to_string() == "|BA" or replica.to_string() == "|AB"
    # deterministic: same delivery order always yields the same text
    replica2 = _peer("R2", crdt)
    replica2.integrate(op_b)
    replica2.integrate(op_a)
    assert replica2.to_string() == replica.to_string()


def test_random_concurrent_edits_converge():
    """Property-style: N rounds of random concurrent edits from two sites,
    delivered to fresh replicas in random (per-site-ordered) interleavings,
    always converge to identical text — including a late-joining third
    replica that replays the full op log in generation order."""

    def interleave(ops_a, ops_b, rng):
        out, i, j = [], 0, 0
        while i < len(ops_a) and j < len(ops_b):
            if rng.random() < 0.5:
                out.append(ops_a[i])
                i += 1
            else:
                out.append(ops_b[j])
                j += 1
        out.extend(ops_a[i:])
        out.extend(ops_b[j:])
        return out

    for seed in range(12):
        rng = random.Random(seed)
        base = TextCRDT("site-base", initial_text="base")
        a = _peer("site-A", base)
        b = _peer("site-B", base)
        ops_a: list[dict] = []
        ops_b: list[dict] = []
        for _ in range(80):
            site, replica, bucket = rng.choice(
                [("site-A", a, ops_a), ("site-B", b, ops_b)]
            )
            n = replica.alive_count
            if rng.random() < 0.65 or n == 0:
                index = rng.randint(0, n)
                sample = rng.choice(UNICODE_SAMPLES)
                text = sample[: rng.randint(0, len(sample))]
                bucket.append(replica.local_insert(index, text))
            else:
                start = rng.randint(0, n - 1)
                end = rng.randint(start + 1, min(n, start + 5))
                bucket.append(replica.local_delete(start, end))

        r1 = _peer("R1", base)
        r2 = _peer("R2", base)
        late = _peer("R3", base)
        for op in interleave(ops_a, ops_b, rng):
            r1.integrate(op)
        for op in interleave(ops_a, ops_b, rng):
            r2.integrate(op)
        for op in ops_a + ops_b:  # late join replays in generation order
            late.integrate(op)
        assert r1.to_string() == r2.to_string(), f"seed {seed}: replicas diverged"
        assert late.to_string() == r1.to_string(), f"seed {seed}: late join diverged"


def test_op_key_is_unique_and_stable():
    a = TextCRDT("A")
    ins1 = a.local_insert(0, "xy")
    ins2 = a.local_insert(2, "z")
    dele = a.local_delete(0, 1)
    keys = {op_key(op) for op in (ins1, ins2, dele)}
    assert len(keys) == 3
    assert op_key(ins1) == ("i", "A", 1, 2)
    assert op_key({"t": "bogus", "s": "x"}) is None
    assert op_key(None) is None


# ----------------------------------------------------------------------
# CollabHub — revisions, dedup, late join, presence, SSE fan-out
# ----------------------------------------------------------------------


def test_hub_applies_ops_and_assigns_revisions():
    reset_hub()
    hub = get_hub()
    op_gen = TextCRDT("client-A")
    op = op_gen.local_insert(0, "hi")
    result = hub.apply_ops("doc.docx", "client-A", [op])
    assert result["rev"] == 1  # no seed op: doc created via first apply
    assert result["text"] == "hi"
    assert len(result["ops"]) == 1


def test_hub_deduplicates_redelivery():
    reset_hub()
    hub = get_hub()
    gen = TextCRDT("client-A")
    op = gen.local_insert(0, "ping")
    first = hub.apply_ops("doc.docx", "client-A", [op])
    second = hub.apply_ops("doc.docx", "client-A", [op])
    assert second["applied"] == []
    assert second["rev"] == first["rev"]
    assert hub.state("doc.docx")["text"] == "ping"


def test_hub_catchup_replay_from_base_rev():
    reset_hub()
    hub = get_hub()
    a = TextCRDT("A")
    b = TextCRDT("B")
    op_a = a.local_insert(0, "AAA")
    hub.apply_ops("doc.docx", "A", [op_a])
    rev1 = hub.rev("doc.docx")
    assert rev1 == 1
    # client B has seen nothing yet (rev 0) and submits an insert; the reply
    # must heal B's gap with A's op too.
    op_b = b.local_insert(0, "BBB")
    result = hub.apply_ops("doc.docx", "B", [op_b], base_rev=0)
    assert any(o.get("chars") == "AAA" for o in result["ops"])
    assert any(o.get("chars") == "BBB" for o in result["ops"])
    # two replicas replaying the full log converge; concurrent inserts with
    # the same anchor interleave deterministically, but the content is the
    # same everywhere
    r1 = TextCRDT("R1")
    r2 = TextCRDT("R2")
    for op in hub.ops_since("doc.docx", 0):
        r1.integrate(op)
        r2.integrate(op)
    assert r1.to_string() == r2.to_string()
    assert sorted(r1.to_string()) == sorted("AAABBB")


def test_hub_two_clients_via_hub_converge():
    reset_hub()
    hub = get_hub()
    hub.resync("shared.docx", "seed")
    base = TextCRDT(BASE_SITE, initial_text="seed")
    a = _peer("A", base)
    b = _peer("B", base)
    # A inserts at the end, B deletes the middle — concurrently.
    op_a = a.local_insert(4, ".")
    rev0 = hub.rev("shared.docx")
    hub.apply_ops("shared.docx", "A", [op_a])
    op_b = b.local_delete(0, 4)
    hub.apply_ops("shared.docx", "B", [op_b], base_rev=rev0)

    r1 = _peer("R1", base)
    r2 = _peer("R2", base)
    for op in hub.ops_since("shared.docx", 0):
        r1.integrate(op)
        r2.integrate(op)
    assert r1.to_string() == r2.to_string()


def test_hub_late_join_replays_full_log():
    reset_hub()
    hub = get_hub()
    hub.resync("doc.docx", "The quick brown fox")
    base = TextCRDT(BASE_SITE, initial_text="The quick brown fox")
    a = _peer("A", base)
    b = _peer("B", base)
    op_a = a.local_insert(10, "red ")
    op_b = b.local_delete(16, 19)
    hub.apply_ops("doc.docx", "A", [op_a])
    hub.apply_ops("doc.docx", "B", [op_b])
    # a brand-new client has never seen the doc: replay everything
    late = TextCRDT("late-joiner")
    for op in hub.ops_since("doc.docx", 0):
        late.integrate(op)
    # independent reference with the same two client ops
    reference = _peer("ref", base)
    reference.integrate(op_a)
    reference.integrate(op_b)
    assert late.to_string() == reference.to_string()
    assert "red" in late.to_string()
    assert "fox" not in late.to_string()


def test_hub_resync_rebases_and_notifies_subscribers():
    reset_hub()
    hub = get_hub()
    hub.resync("doc.docx", "old content")
    hub.apply_ops("doc.docx", "A", [TextCRDT("A").local_insert(0, "x")])
    queue = hub.subscribe("doc.docx")
    state = hub.resync("doc.docx", "brand new")
    assert state["rev"] == 1
    assert state["text"] == "brand new"
    assert hub.rev("doc.docx") == 1
    assert "resync" in queue.get_nowait()  # subscribers are told to rebase


def test_hub_presence_cursors_and_leave():
    reset_hub()
    hub = get_hub()
    hub.set_presence("doc.docx", "c1", user="Ada", cursor={"index": 3})
    hub.set_presence("doc.docx", "c2", user="Alan", cursor={"index": 0})
    clients = hub.clients("doc.docx")
    assert len(clients) == 2
    assert {c["client"] for c in clients} == {"c1", "c2"}
    assert {c["user"] for c in clients} == {"Ada", "Alan"}
    # leaving (empty cursor) removes the editor
    hub.set_presence("doc.docx", "c1", cursor=None)
    assert [c["client"] for c in hub.clients("doc.docx")] == ["c2"]


def test_hub_broadcast_fans_out_to_subscribers():
    reset_hub()
    hub = get_hub()
    q1 = hub.subscribe("doc.docx")
    q2 = hub.subscribe("doc.docx")
    gen = TextCRDT("A")
    hub.apply_ops("doc.docx", "A", [gen.local_insert(0, "zap")])
    ev1 = json.loads(q1.get_nowait())
    ev2 = json.loads(q2.get_nowait())
    assert ev1["type"] == "ops"
    assert ev2["type"] == "ops"
    assert ev1["ops"][0]["chars"] == "zap"


def test_hub_unsubscribe_stops_events():
    reset_hub()
    hub = get_hub()
    q = hub.subscribe("doc.docx")
    hub.unsubscribe("doc.docx", q)
    gen = TextCRDT("A")
    hub.apply_ops("doc.docx", "A", [gen.local_insert(0, "x")])
    assert q.empty()


def test_seed_op_uses_base_site():
    reset_hub()
    hub = get_hub()
    hub.resync("doc.docx", "abc")
    ops = hub.ops_since("doc.docx", 0)
    assert ops[0]["s"] == BASE_SITE


# ----------------------------------------------------------------------
# HTTP API
# ----------------------------------------------------------------------


def test_state_endpoint_seeds_from_store(client):
    _seed_store(client, "doc.docx", "Hello world")
    resp = client.get("/api/documents/doc.docx/collab/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["doc_id"] == "doc.docx"
    # The collaboration base is plain text (not HTML): CRDT positions are
    # visible-character indices, exactly what a browser editor exposes.
    assert data["text"] == "Hello world"
    assert data["rev"] == 1
    assert len(data["ops"]) == 1


def test_apply_ops_endpoint(client):
    _collab_base(client, text="Hello world")
    gen = _editor(client, "doc.docx", "editor-1")
    op = gen.local_insert(3, "!")  # right after the 3rd visible char
    expected = gen.to_string()
    resp = client.post(
        "/api/documents/doc.docx/collab/ops",
        json={"client_id": "editor-1", "base_rev": 1, "ops": [op]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rev"] == 2
    assert data["text"] == expected
    assert len(data["applied"]) == 1


def test_apply_ops_heals_gap_in_single_roundtrip(client):
    _collab_base(client, text="Hello")
    gen_a = _editor(client, "doc.docx", "editor-A")
    gen_b = _editor(client, "doc.docx", "editor-B")
    op_a = gen_a.local_insert(3, " A")
    op_b = gen_b.local_insert(3, " B")
    client.post("/api/documents/doc.docx/collab/ops", json={"client_id": "editor-A", "ops": [op_a]})
    # B is still on rev 1; its reply must contain A's op for catch-up
    resp = client.post(
        "/api/documents/doc.docx/collab/ops",
        json={"client_id": "editor-B", "base_rev": 1, "ops": [op_b]},
    )
    data = resp.json()
    caught_up = [o for o in data["ops"] if o.get("chars") == " A"]
    assert caught_up, "B should receive A's op as catch-up"


def test_ops_since_endpoint(client):
    _collab_base(client, text="Hi")
    gen = _editor(client, "doc.docx", "editor-1")
    op = gen.local_insert(3, "!")
    client.post("/api/documents/doc.docx/collab/ops", json={"client_id": "editor-1", "ops": [op]})
    resp = client.get("/api/documents/doc.docx/collab/ops?since=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rev"] == 2
    assert len(data["ops"]) == 1
    assert data["ops"][0]["chars"] == "!"
    # full replay from scratch returns the seed op too
    full = client.get("/api/documents/doc.docx/collab/ops?since=0").json()
    assert len(full["ops"]) == 2


def test_presence_endpoints(client):
    _seed_store(client, "doc.docx")
    resp = client.post(
        "/api/documents/doc.docx/collab/presence",
        json={"client_id": "c1", "user": "Ada", "cursor": {"index": 2}},
    )
    assert resp.status_code == 200
    listed = client.get("/api/documents/doc.docx/collab/presence").json()
    assert [c["client"] for c in listed["clients"]] == ["c1"]
    # leave
    client.post("/api/documents/doc.docx/collab/presence", json={"client_id": "c1", "cursor": None})
    listed = client.get("/api/documents/doc.docx/collab/presence").json()
    assert listed["clients"] == []


def test_presence_requires_client_id(client):
    resp = client.post("/api/documents/doc.docx/collab/presence", json={"user": "nobody"})
    assert resp.status_code == 400


def test_resync_endpoint(client):
    _seed_store(client, "doc.docx", "old")
    resp = client.post(
        "/api/documents/doc.docx/collab/resync", json={"text": "after-save"}
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "after-save"
    assert resp.json()["rev"] == 1


def test_apply_ops_invalid_json_returns_400(client):
    _seed_store(client, "doc.docx")
    resp = client.post(
        "/api/documents/doc.docx/collab/ops",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/documents/doc.docx/collab/ops", json={"ops": "nope"}
    )
    assert resp.status_code == 400


def test_sse_stream_emits_initial_state(server_client):
    client = server_client
    _seed_store(client, "doc.docx", "stream seed")
    with client.stream("GET", "/api/documents/doc.docx/collab/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = ""
        for line in resp.iter_lines():
            lines += line + "\n"
            if "data:" in lines:
                break  # the SSE stream is endless — stop after the snapshot
    assert "event: state" in lines
    assert "stream seed" in lines


def test_sse_stream_receives_live_ops(server_client):
    """A subscribed client receives ops pushed by another editor."""
    client = server_client
    _seed_store(client, "doc.docx", "live")
    received: list[str] = []
    marker = "liveop"
    done = threading.Event()

    def read_stream():
        with client.stream("GET", "/api/documents/doc.docx/collab/stream") as resp:
            for line in resp.iter_lines():
                received.append(line)
                if marker in line:
                    done.set()
                    return

    thread = threading.Thread(target=read_stream, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert received, "expected at least the initial state event"
    assert any("event: state" in e for e in received)

    # another editor applies an op while the first client is subscribed
    gen = TextCRDT("editor-zap")
    gen.local_insert(0, "liveop")
    op = {
        "t": "insert",
        "s": "editor-zap",
        "b": 1,
        "n": 6,
        "chars": "liveop",
        "originSite": "",
        "originSeq": 0,
    }
    resp = client.post(
        "/api/documents/doc.docx/collab/ops",
        json={"client_id": "editor-zap", "ops": [op]},
    )
    assert resp.status_code == 200

    assert done.wait(timeout=10), "subscriber never received the live op"
    assert marker in "".join(received)
