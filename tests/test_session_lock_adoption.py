"""Editor-session lock adoption against the stub WOPI host + SessionRegistry semantics.

Area: ``src/editor/session.py`` — ``RemoteWopiClient.acquire_or_adopt_lock``
(and its siblings get/put/release) exercised against the in-repo **stub WOPI
host** (``src/wopi/testhost.py``), plus the ``SessionRegistry`` guarantees
that let a writable session and a read-only session for the SAME document
coexist.

The adoption contract under test (matching the docstring in
``acquire_or_adopt_lock``):

* first acquire on an unlocked file -> mint ``wo:{owner}:{uuid}``, writable;
* same owner (re-open / orphan from this user's crashed session) -> adopt the
  existing token, stay writable, share it across the user's sessions;
* different owner (another user editing) -> return ``("", False)`` so the
  session is served read-only; the foreign lock is never stolen;
* legacy / unknown-format lock -> deliberately taken over with an owner-named
  token so LATER cross-user opens can be enforced.

The stub host runs on a **pre-bound loopback socket** handed to uvicorn, so
the suite needs no polling, no sleeps and no external network: the kernel
accepts connections into the backlog until the server thread's event loop is
ready. There is no time-of-day dependence (locks are named with ``uuid``, and
GET_LOCK state is asserted, never timestamps).

Paradigms:

* **Unit tests** — one concept each: fresh-lock acquisition, same-owner
  adoption, adopted-lock saves, cross-user read-only rejection (and the
  resulting 409 on save), legacy-lock take-over, ownerless tokens, lock
  release, and registry coexistence/state-isolation around the resulting
  sessions.

* **Hypothesis property test** — arbitrary sequences of owner-named
  acquisition attempts against the stub host must always satisfy the
  adoption contract, checked step by step against the host's real lock state.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest
import uvicorn
from hypothesis import given, settings
from hypothesis import strategies as st

from src.editor.session import EditorSession, RemoteWopiClient, SessionRegistry
from src.wopi.testhost import app as mock_host_app
from src.wopi.testhost import reset_store

# ---------------------------------------------------------------------------
# Stub host harness (loopback, pre-bound socket: no polling, no sleeps)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def host_url() -> str:
    """Run the stub WOPI host on a pre-bound loopback socket.

    The socket is created, bound and put into LISTEN state synchronously
    BEFORE the server thread starts, so the port is known immediately and the
    kernel queues every connection attempt until uvicorn's event loop accepts
    it. No readiness polling, no sleeps, no external network.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)

    server = uvicorn.Server(uvicorn.Config(mock_host_app, log_level="error"))
    thread = threading.Thread(target=server.run, args=([sock],), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(autouse=True)
def _clean_host_store():
    """Isolate the stub host's module-level store between tests."""
    reset_store()
    yield
    reset_store()


def _seed(
    host_url: str,
    *,
    name: str = "hello.docx",
    data: bytes = b"hello world",
    doc_id: str | None = None,
) -> dict:
    """Create a file on the stub host; returns {id, access_token, name}."""
    payload: dict = {"name": name, "data": base64.b64encode(data).decode()}
    if doc_id is not None:
        payload["id"] = doc_id
    req = urllib.request.Request(
        f"{host_url}/_host/files",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _host_lock(host_url: str, doc_id: str, access_token: str) -> str:
    """GET_LOCK on the stub host; ``" "`` (single space) means unlocked."""
    req = urllib.request.Request(
        f"{host_url}/wopi/files/{doc_id}?access_token={access_token}",
        method="POST",
        headers={"X-WOPI-Override": "GET_LOCK"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.headers.get("X-WOPI-Lock", "")


def _host_bytes(host_url: str, doc_id: str, access_token: str) -> bytes:
    """GetFile on the stub host (raw stored bytes)."""
    with urllib.request.urlopen(
        f"{host_url}/wopi/files/{doc_id}/contents?access_token={access_token}",
        timeout=10,
    ) as resp:
        return resp.read()


def _set_host_lock(host_url: str, doc_id: str, access_token: str, lock: str) -> None:
    """Install a lock on the stub host directly (bypassing RemoteWopiClient)."""
    req = urllib.request.Request(
        f"{host_url}/wopi/files/{doc_id}?access_token={access_token}",
        method="POST",
        headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": lock},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _lock_owner(lock: str) -> str:
    """The owner encoded in a ``wo:{owner}:{uuid}`` token, or ``""``."""
    return lock.split(":", 2)[1] if lock.startswith("wo:") else ""


# ---------------------------------------------------------------------------
# Fresh acquisition on an unlocked file
# ---------------------------------------------------------------------------

def test_acquire_lock_on_unlocked_file_wins(host_url):
    """On an unlocked file the first acquire mints an owner-named lock,
    reports writable, and the host ends up holding exactly that token."""
    seeded = _seed(host_url, doc_id="fresh-doc")
    client = RemoteWopiClient(host_url, seeded["access_token"])

    lock, writable = client.acquire_or_adopt_lock(seeded["id"], owner="alice")

    assert writable is True
    assert lock.startswith("wo:alice:")
    assert client.lock_token == lock
    # the host now holds the very token we were granted
    assert _host_lock(host_url, seeded["id"], seeded["access_token"]) == lock


def test_ownerless_acquire_mints_plain_token(host_url):
    """When no owner is known the client still locks the file (PutFile on an
    unlocked file would be refused), but mints a plain token WITHOUT the
    ``wo:`` owner marker — a later owner-named client will then take it over."""
    seeded = _seed(host_url, doc_id="ownerless")
    client = RemoteWopiClient(host_url, seeded["access_token"])

    lock, writable = client.acquire_or_adopt_lock(seeded["id"], owner="")

    assert writable is True
    assert not lock.startswith("wo:")
    assert _host_lock(host_url, seeded["id"], seeded["access_token"]) == lock


# ---------------------------------------------------------------------------
# Same-owner adoption (re-open, second tab, orphaned session)
# ---------------------------------------------------------------------------

def test_same_owner_adopts_existing_lock_and_shares_token(host_url):
    """A second launch by the SAME owner (another tab, or an orphan left by
    this user's crashed session) adopts the existing lock: the same token
    comes back, still writable, host lock unchanged — so every session of one
    user keeps saving against a single shared lock."""
    seeded = _seed(host_url, doc_id="same-owner")
    first = RemoteWopiClient(host_url, seeded["access_token"])
    lock1, w1 = first.acquire_or_adopt_lock(seeded["id"], owner="alice")
    assert w1 is True

    second = RemoteWopiClient(host_url, seeded["access_token"])
    lock2, w2 = second.acquire_or_adopt_lock(seeded["id"], owner="alice")

    assert w2 is True
    assert lock2 == lock1  # adopted, NOT a fresh mint
    assert second.lock_token == lock1
    assert _host_lock(host_url, seeded["id"], seeded["access_token"]) == lock1


def test_adopted_lock_lets_both_sessions_save(host_url):
    """Adoption is not cosmetic: both the original and the adopting session
    can PutFile with the shared token — the stub host accepts either lock."""
    seeded = _seed(host_url, doc_id="adopt-save", data=b"original")
    first = RemoteWopiClient(host_url, seeded["access_token"])
    _, w1 = first.acquire_or_adopt_lock(seeded["id"], owner="alice")
    assert w1 is True

    second = RemoteWopiClient(host_url, seeded["access_token"])
    lock2, w2 = second.acquire_or_adopt_lock(seeded["id"], owner="alice")
    assert w2 is True and lock2 == first.lock_token

    # both sessions push edits under the same adopted lock, no 409
    second.put_contents(seeded["id"], b"edited by second tab")
    first.put_contents(seeded["id"], b"edited by first tab")

    # host bytes reflect the last successful save
    assert _host_bytes(host_url, seeded["id"], seeded["access_token"]) == b"edited by first tab"


# ---------------------------------------------------------------------------
# Cross-user rejection -> read-only
# ---------------------------------------------------------------------------

def test_different_owner_is_rejected_read_only(host_url):
    """A lock held by another user is NOT stolen: acquire returns writable
    False with an empty token, the host lock stays with its original owner,
    and the rejected client carries no lock_token."""
    seeded = _seed(host_url, doc_id="cross-user")
    alice = RemoteWopiClient(host_url, seeded["access_token"])
    lock_a, _ = alice.acquire_or_adopt_lock(seeded["id"], owner="alice")

    bob = RemoteWopiClient(host_url, seeded["access_token"])
    lock_b, writable_b = bob.acquire_or_adopt_lock(seeded["id"], owner="bob")

    assert writable_b is False
    assert lock_b == ""
    assert bob.lock_token == ""
    assert _host_lock(host_url, seeded["id"], seeded["access_token"]) == lock_a


def test_rejected_client_put_is_refused_and_data_untouched(host_url):
    """The read-only consequence is real at the wire level: a rejected session
    holds no token, so its PutFile is refused with 409 and the file bytes are
    left exactly as the lock owner saved them."""
    seeded = _seed(host_url, doc_id="readonly-save", data=b"alice data")
    alice = RemoteWopiClient(host_url, seeded["access_token"])
    alice.acquire_or_adopt_lock(seeded["id"], owner="alice")

    bob = RemoteWopiClient(host_url, seeded["access_token"])
    _, writable = bob.acquire_or_adopt_lock(seeded["id"], owner="bob")
    assert writable is False

    with pytest.raises(urllib.error.HTTPError):
        bob.put_contents(seeded["id"], b"bob overwrite")

    assert _host_bytes(host_url, seeded["id"], seeded["access_token"]) == b"alice data"


# ---------------------------------------------------------------------------
# Legacy / unknown-format lock take-over
# ---------------------------------------------------------------------------

def test_legacy_lock_is_taken_over_with_owner_named_token(host_url):
    """A legacy lock that carries no ``wo:{owner}:`` marker (pre-upgrade or a
    crashed session) is deliberately taken over: unlocked and re-locked with
    an owner-named token so that LATER cross-user opens can be enforced."""
    seeded = _seed(host_url, doc_id="legacy")
    _set_host_lock(host_url, seeded["id"], seeded["access_token"], "LEGACY-LOCK-42")

    alice = RemoteWopiClient(host_url, seeded["access_token"])
    lock, writable = alice.acquire_or_adopt_lock(seeded["id"], owner="alice")

    assert writable is True
    assert lock.startswith("wo:alice:")
    assert _host_lock(host_url, seeded["id"], seeded["access_token"]) == lock

    # now that the lock is owner-named, a DIFFERENT owner is refused
    bob = RemoteWopiClient(host_url, seeded["access_token"])
    _, writable_bob = bob.acquire_or_adopt_lock(seeded["id"], owner="bob")
    assert writable_bob is False


# ---------------------------------------------------------------------------
# Lock release
# ---------------------------------------------------------------------------

def test_release_lock_clears_host_lock_and_client_token(host_url):
    """release_lock is best-effort but effective against the stub host: the
    host reports unlocked afterwards and the client forgets its token, so a
    fresh acquire starts from a clean slate."""
    seeded = _seed(host_url, doc_id="release")
    client = RemoteWopiClient(host_url, seeded["access_token"])
    lock, _ = client.acquire_or_adopt_lock(seeded["id"], owner="alice")
    assert _host_lock(host_url, seeded["id"], seeded["access_token"]) == lock

    client.release_lock(seeded["id"])

    assert client.lock_token == ""
    # the WOPI unlocked sentinel (" ") arrives as an empty string once the
    # HTTP layer strips header whitespace — treat both as unlocked
    assert _host_lock(host_url, seeded["id"], seeded["access_token"]).strip() == ""


# ---------------------------------------------------------------------------
# SessionRegistry semantics around the resulting sessions
# ---------------------------------------------------------------------------

def _session(
    doc_id: str,
    *,
    created_at: float,
    user_id: str,
    read_only: bool = False,
    lock_token: str = "",
) -> EditorSession:
    """A deterministic EditorSession, mirroring what the router would build
    from an ``acquire_or_adopt_lock`` outcome (writable -> lock_token set,
    rejected -> read_only + empty lock_token)."""
    return EditorSession(
        doc_id=doc_id,
        name=f"{doc_id}.docx",
        size=10,
        version="1",
        last_modified=0,
        user_id=user_id,
        owner_id=user_id,
        lock_token=lock_token,
        read_only=read_only,
        created_at=created_at,
    )


def test_registry_coexists_writable_and_read_only_sessions_for_same_doc():
    """A writable session (owner holding the lock) and a read-only session
    (owner rejected for that lock) for the SAME document coexist in the
    registry: both keyed by unique session id, never clobbering, and the
    doc-level ``get()`` resolves the most recently launched one."""
    reg = SessionRegistry()
    alice = _session("doc1", created_at=1000.0, user_id="alice", lock_token="wo:alice:abc")
    bob = _session("doc1", created_at=1001.0, user_id="bob", read_only=True)

    reg.register(alice)
    reg.register(bob)

    assert len(reg.all()) == 2
    # id-exact resolution: never the other user's session
    assert reg.get_by_id(alice.session_id) is alice
    assert reg.get_by_id(bob.session_id) is bob
    # the doc-level shortcut resolves the MOST RECENT launch
    assert reg.get("doc1") is bob
    # lock state is preserved per session, not merged
    assert reg.get_by_id(alice.session_id).lock_token == "wo:alice:abc"
    assert reg.get_by_id(alice.session_id).read_only is False
    assert reg.get_by_id(bob.session_id).lock_token == ""
    assert reg.get_by_id(bob.session_id).read_only is True


def test_registry_drop_expires_all_sessions_of_doc_keeping_others():
    """``drop()`` expires every live session of a document — writable and
    read-only alike — while leaving unrelated documents' sessions intact."""
    reg = SessionRegistry()
    alice = _session("doc1", created_at=1000.0, user_id="alice", lock_token="wo:alice:abc")
    bob = _session("doc1", created_at=1001.0, user_id="bob", read_only=True)
    carol = _session("doc2", created_at=1002.0, user_id="carol", lock_token="wo:carol:def")
    reg.register(alice)
    reg.register(bob)
    reg.register(carol)

    reg.drop("doc1")

    assert reg.get("doc1") is None
    assert reg.get_by_id(alice.session_id) is None
    assert reg.get_by_id(bob.session_id) is None
    assert reg.get_by_id(carol.session_id) is carol  # untouched
    assert reg.all() == [carol]


# ---------------------------------------------------------------------------
# Property test: adoption contract over arbitrary owner sequences
# ---------------------------------------------------------------------------

_OWNERS = st.sampled_from(["alice", "bob", ""])


@given(st.lists(_OWNERS, min_size=0, max_size=10))
@settings(deadline=None, max_examples=25)
def test_lock_adoption_contract_holds_over_random_sequences(host_url, owners: list[str]) -> None:
    """ANY sequence of owner-named acquisition attempts against the stub host
    satisfies the adoption contract, checked step by step against the host's
    real GET_LOCK state:

    * a rejected cross-owner acquire returns (``""``, False) and NEVER
      changes the host lock;
    * a same-owner acquire returns the existing token unchanged (writable);
    * any successful acquire returns exactly the token the host ends up
      holding, and engrains it on the client.
    """
    seeded = _seed(host_url, doc_id="property-doc", data=b"property seed")

    def host_lock() -> str:
        lock = _host_lock(host_url, seeded["id"], seeded["access_token"])
        return "" if not lock.strip() else lock  # normalize the " " sentinel

    for owner in owners:
        current = host_lock()
        client = RemoteWopiClient(host_url, seeded["access_token"])
        lock, writable = client.acquire_or_adopt_lock(seeded["id"], owner=owner)
        after = host_lock()

        own = _lock_owner(current)
        if owner and own and own != owner:
            # different owner: read-only, host lock untouched
            assert (lock, writable) == ("", False), (owner, current)
            assert client.lock_token == ""
        elif current.startswith("wo:"):
            # same owner (incl. owner-unknown client): adopt unchanged token
            assert writable is True
            assert lock == current
            assert client.lock_token == current
        else:
            # fresh file or legacy-format lock: taken over, writable
            assert writable is True
            assert lock and lock != current
            assert client.lock_token == lock
            if owner:
                assert lock.startswith(f"wo:{owner}:")
            else:
                assert not lock.startswith("wo:")

        # host converges to the returned token on success, untouched on reject
        if writable:
            assert after == lock
        else:
            assert after == current
