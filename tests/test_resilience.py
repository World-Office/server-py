"""Resilience and fault-injection testing of the docserver.

Distinct paradigms complementing the other suites:

* **Hostile-bytes read-path fuzzing** — every converter reader
  (``docx_to_html``, ``odt_to_html``) is fed arbitrary random bytes, plus
  truncated/corrupted versions of otherwise-valid documents. Invariant: it
  never raises and always returns ``str`` — no 500s from hostile or corrupt
  documents, ever.

* **Filesystem fault injection** — content files missing or corrupted on
  disk (row present in the index, bytes gone/garbage), updating content
  after such damage, deleting absent documents: the store must degrade
  gracefully, not raise.

* **Protocol fault injection** — oversized PutFile bodies are rejected with
  413, not stored; the happy path is unaffected.

* **Concurrency** — simultaneous readers/writers on a shared store must not
  corrupt state or raise (the store's contract is "last write wins, never
  crashes").

* **Malicious/abusive collaboration inputs** — malformed, mis-typed and
  hostile op payloads are rejected or harmlessly skipped, and the hub must
  remain healthy and consistent for legitimate editors afterwards.

* **Broken-subscriber fault injection** — a subscriber whose queue raises
  on delivery is dropped without breaking the hub's fan-out to the rest.
"""

from __future__ import annotations

import io
import json
import threading
from contextlib import asynccontextmanager

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import Config
from src.editor.collab import get_hub, reset_hub
from src.editor.converter import docx_to_html
from src.editor.odt_converter import html_to_odt, odt_to_html
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router


def _docx_bytes(text: str = "Hello world") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


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
    app.include_router(wopi_router)
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


# ---------------------------------------------------------------------------
# 1. Hostile-bytes read-path fuzzing
# ---------------------------------------------------------------------------

_READ_FUZZ = settings(max_examples=150, deadline=None)


@given(data=st.binary(min_size=0, max_size=2048))
@_READ_FUZZ
def test_docx_reader_never_raises_on_arbitrary_bytes(data: bytes):
    """Whatever bytes arrive (corrupt, truncated, garbage, or a totally
    different format), the DOCX reader must return str, not raise."""
    out = docx_to_html(data)
    assert isinstance(out, str)


@given(data=st.binary(min_size=0, max_size=2048))
@_READ_FUZZ
def test_odt_reader_never_raises_on_arbitrary_bytes(data: bytes):
    out = odt_to_html(data)
    assert isinstance(out, str)


@given(k=st.integers(min_value=0, max_value=2048), flip=st.integers(min_value=0, max_value=64))
@_READ_FUZZ
def test_truncated_and_corrupted_docx_never_raise(k: int, flip: int):
    """Truncation + bit-flip corruption of a real DOCX must not raise."""
    good = _docx_bytes("Some real document content для теста")
    data = bytearray(good[: min(k, len(good))])
    for _ in range(flip):
        if data:
            data[min(len(data) - 1, flip % len(data))] ^= 0xFF  # deterministic-ish
    assert isinstance(docx_to_html(bytes(data)), str)


@given(k=st.integers(min_value=0, max_value=2048))
@_READ_FUZZ
def test_truncated_odt_never_raises(k: int):
    good = html_to_odt("<p>real <b>odt</b> content</p>")
    out = odt_to_html(good[: min(k, len(good))])
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# 2. Filesystem fault injection (store layer)
# ---------------------------------------------------------------------------


def test_missing_content_file_degrades_gracefully(tmp_path):
    store = DocumentStore(str(tmp_path / "f.db"), str(tmp_path / "content"))
    store.init("r1", "r1.docx")
    store.put_content("r1", b"hello")
    # Attack: content bytes vanish from disk (row survives in the index).
    store.content_path("r1").unlink()
    assert store.get_content("r1") is None      # no crash, byte-less read
    assert store.get("r1") is not None          # metadata still available
    # Recovery: writing again recreates the file.
    store.put_content("r1", b"again")
    assert store.get_content("r1") == b"again"


def test_corrupted_content_bytes_are_served_not_crashed(tmp_path):
    store = DocumentStore(str(tmp_path / "f.db"), str(tmp_path / "content"))
    store.init("r2", "r2.docx")
    store.put_content("r2", b"good")
    store.content_path("r2").write_bytes(b"\x00\xffgarbage" * 10)  # disk corruption
    assert store.get_content("r2") == b"\x00\xffgarbage" * 10
    assert store.get("r2") is not None


def test_missing_content_file_returns_404_not_500(client):
    store = client.test_store  # type: ignore[attr-defined]
    store.init("gone", "gone.docx")
    store.put_content("gone", b"data")
    store.content_path("gone").unlink()
    res = client.get("/wopi/files/gone/contents")
    assert res.status_code == 404


def test_delete_absent_document_is_false(tmp_path):
    store = DocumentStore(str(tmp_path / "f.db"), str(tmp_path / "content"))
    assert store.delete("nope") is False
    assert store.get("nope") is None


def test_http_413_oversized_putfile_rejected(client, monkeypatch):
    import src.wopi.router as wr

    store = client.test_store  # type: ignore[attr-defined]
    store.init("big", "big.docx")
    store.put_content("big", b"small")
    monkeypatch.setattr(wr, "MAX_FILE_SIZE", 16)  # shrink the limit for the test
    res = client.post("/wopi/files/big/contents", content=b"x" * 32)
    assert res.status_code == 413
    # the stored content must be untouched
    assert store.get_content("big") == b"small"
    # a small body still works under the fault-injected limit
    res = client.post("/wopi/files/big/contents", content=b"ok")
    assert res.status_code == 200
    assert store.get_content("big") == b"ok"


# ---------------------------------------------------------------------------
# 3. Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_writers_and_readers_do_not_corrupt_store(tmp_path):
    store = DocumentStore(str(tmp_path / "c.db"), str(tmp_path / "content"))
    store.init("conc", "conc.docx")
    payloads = [f"payload-{i}-".encode() * 64 for i in range(8)]
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(i: int) -> None:
        try:
            barrier.wait()  # maximize contention
            for _ in range(6):
                store.put_content("conc", payloads[i])
                assert store.get_content("conc") is not None
                store.set_lock("conc", f"lock-{i}")
                assert store.get_lock("conc") in {f"lock-{j}" for j in range(8)}
        except BaseException as exc:  # noqa: BLE001 - record any failure
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, f"concurrent store access raised: {errors!r}"
    assert store.get_content("conc") in payloads  # last write wins, no corrupt byte


def test_concurrent_distinct_documents_do_not_interfere(tmp_path):
    store = DocumentStore(str(tmp_path / "c.db"), str(tmp_path / "content"))
    for i in range(6):
        store.init(f"doc{i}", f"doc{i}.docx")
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            for _ in range(5):
                store.put_content(f"doc{i}", f"mine-{i}-".encode() * 16)
                assert store.get_content(f"doc{i}") == f"mine-{i}-".encode() * 16
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, f"concurrent per-doc access raised: {errors!r}"


# ---------------------------------------------------------------------------
# 4. Malicious / abusive collaboration inputs
# ---------------------------------------------------------------------------

_BAD_COLLAB_PAYLOADS: list[tuple[bytes, int, str]] = [
    (b"not json", 400, "invalid json"),
    (b"[]", 400, "body not an object"),
    (b"{}", 400, "ops missing"),
    (b'{"ops": "nope"}', 400, "ops not a list"),
    (b'{"client_id": {"evil": 1}, "ops": [{"t":"insert","s":"a","b":1,"n":1,'
     b'"chars":"x","originSite":"","originSeq":0}]}', 200, "non-str client id"),
    (b'{"ops": [42, "x", null]}', 200, "non-dict ops skipped"),
    (b'{"ops": [{"t":"delete","ids":"bad"}]}', 200, "bad ids shape skipped"),
    (b'{"ops": [{"t":"insert","s":"","b":null,"n":null,"chars":"x"}]}', 200, "null fields"),
    (b'{"ops": [{"t":"bogus","s":"a"}]}', 200, "unknown op type skipped"),
]


@pytest.mark.parametrize("payload,expected,why", _BAD_COLLAB_PAYLOADS)
def test_malicious_collab_payloads_are_rejected_or_skipped(client, payload, expected, why):
    res = client.post("/api/documents/attacked/collab/ops", content=payload)
    assert res.status_code == expected, f"{why}: got {res.status_code}"
    # After the hostile input, a legitimate editor must still work and the
    # hub must be consistent (no partial corruption, no crash).
    good = json.dumps(
        {"client_id": "good-editor", "ops": [
            {"t": "insert", "s": "good-editor", "b": 1, "n": 4, "chars": "fine",
             "originSite": "", "originSeq": 0},
        ]}
    )
    res = client.post("/api/documents/attacked/collab/ops", content=good)
    assert res.status_code == 200
    body = res.json()
    assert body["doc_id"] == "attacked"
    state = client.get("/api/documents/attacked/collab/state").json()
    # The legitimate editor's content must be present; structurally-valid
    # earlier ops (e.g. a made-up site id that is otherwise well-formed) may
    # legitimately have been applied too, so do not demand an exact text.
    assert "fine" in state["text"]


def test_empty_and_malformed_json_bodies_are_400(client):
    assert client.post("/api/documents/x/collab/ops", content=b"").status_code == 400
    assert client.post("/api/documents/x/collab/ops", content=b"{").status_code == 400
    assert client.post("/api/documents/x/collab/ops", content=b'{"ops":').status_code == 400


# ---------------------------------------------------------------------------
# 5. Broken-subscriber fault injection
# ---------------------------------------------------------------------------


def test_broken_subscriber_is_dropped_without_breaking_fanout(tmp_path):
    reset_hub()
    hub = get_hub()
    good_queue = hub.subscribe("d-broken")
    good_events: list[str] = []

    class _BrokenQueue:
        def put_nowait(self, payload):
            raise RuntimeError("subscriber connection closed")

    # Inject a broken subscriber directly next to a healthy one.
    hub._subscribers["d-broken"] = {good_queue, _BrokenQueue()}
    # A live edit must reach the healthy subscriber and not raise.
    reply = hub.apply_ops(
        "d-broken",
        "c1",
        [
            {"t": "insert", "s": "c1", "b": 1, "n": 1, "chars": "z",
             "originSite": "", "originSeq": 0},
        ],
    )
    assert reply["applied"], "edit must apply"
    assert not good_queue.empty(), "healthy subscriber must receive the event"
    while not good_queue.empty():
        good_events.append(good_queue.get_nowait())
    assert any("d-broken" in e for e in good_events)
    # and the broken subscriber has been evicted
    subs = hub._subscribers.get("d-broken", set())
    assert not any(isinstance(s, _BrokenQueue) for s in subs)


def test_abandoned_subscriber_does_not_block_later_edits(tmp_path):
    reset_hub()
    hub = get_hub()
    hub.subscribe("d-lonely")  # never drained, never unsubscribed
    # edits keep flowing and the hub stays healthy even with an orphaned queue
    reply = hub.apply_ops(
        "d-lonely",
        "c2",
        [
            {"t": "insert", "s": "c2", "b": 1, "n": 3, "chars": "abc",
             "originSite": "", "originSeq": 0},
        ],
    )
    assert reply["applied"]
    assert hub.state("d-lonely")["text"] == "abc"
