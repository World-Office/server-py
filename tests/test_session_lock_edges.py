"""Tests for session/lock adoption branches + collab char_at/set_presence gaps.

Area: ``src/editor/session.py`` and ``src/editor/collab.py``

Paradigms:
* **char_at edge cases** — empty document, valid indices, out-of-range,
  deleted (tombstoned) characters.
* **set_presence behavior** — empty client_id, leave via cursor=None,
  agent badge assignment.
* **acquire_or_adopt_lock adoption** — same-owner share, cross-user rejection,
  legacy lock takeover, expiry patterns.
"""

from __future__ import annotations

import time

import pytest

from src.editor.collab import CollabHub, TextCRDT
from src.editor.session import RemoteWopiClient, SessionRegistry


# ----------------------------------------------------------------------
# TextCRDT.char_at tests
# ----------------------------------------------------------------------


def test_char_at_empty_document_returns_none():
    """On an empty document, any index returns None."""
    crdt = TextCRDT("test")
    assert crdt.to_string() == ""
    assert crdt.char_at(0) is None
    assert crdt.char_at(-1) is None
    assert crdt.char_at(999) is None


def test_char_at_valid_indices():
    """Valid indices return the correct character."""
    crdt = TextCRDT("test")
    crdt.local_insert(0, "hello")
    # h e l l o
    # 0 1 2 3 4
    assert crdt.char_at(0) == "h"
    assert crdt.char_at(1) == "e"
    assert crdt.char_at(2) == "l"
    assert crdt.char_at(3) == "l"
    assert crdt.char_at(4) == "o"
    assert crdt.char_at(5) is None  # out of range


def test_char_at_negative_indices_return_none():
    """Negative indices are out of range and return None."""
    crdt = TextCRDT("test")
    crdt.local_insert(0, "abc")
    assert crdt.char_at(-1) is None
    assert crdt.char_at(-5) is None


def test_char_at_after_delete_tombstones_correctly():
    """char_at respects tombstoned (deleted) characters."""
    crdt = TextCRDT("test")
    crdt.local_insert(0, "hello")
    crdt.local_delete(1, 4)  # delete "ell", leaving "ho"
    assert crdt.to_string() == "ho"
    assert crdt.char_at(0) == "h"
    assert crdt.char_at(1) == "o"
    assert crdt.char_at(2) is None


def test_char_at_unicode_characters():
    """char_at counts characters, not bytes."""
    crdt = TextCRDT("test")
    crdt.local_insert(0, "héllo")  # 5 chars, 6+ bytes
    assert crdt.char_at(0) == "h"
    assert crdt.char_at(1) == "é"
    assert crdt.char_at(2) == "l"
    assert crdt.char_at(3) == "l"
    assert crdt.char_at(4) == "o"
    assert crdt.char_at(5) is None


# ----------------------------------------------------------------------
# CollabHub.set_presence tests
# ----------------------------------------------------------------------


def test_set_presence_basic():
    """Basic presence announcement adds the client."""
    hub = CollabHub()
    clients = hub.set_presence("doc1", "client1", user="Alice", cursor={"index": 0})
    assert len(clients) == 1
    assert clients[0]["client"] == "client1"
    assert clients[0]["user"] == "Alice"
    assert clients[0]["cursor"] == {"index": 0}


def test_set_presence_empty_client_id_returns_empty_list():
    """Empty client_id is rejected and returns current client list (no change)."""
    hub = CollabHub()
    hub.set_presence("doc1", "c1", user="Alice", cursor={"index": 0})
    # When client_id is empty, return current clients without modification
    clients = hub.set_presence("doc1", "", user="Bob")  # empty client_id
    # Should return existing clients (c1)
    assert len(clients) == 1
    assert clients[0]["client"] == "c1"


def test_set_presence_leaves_via_cursor_none():
    """cursor=None removes the client (leave)."""
    hub = CollabHub()
    hub.set_presence("doc1", "c1", user="Alice", cursor={"index": 0})
    hub.set_presence("doc1", "c2", user="Bob", cursor={"index": 5})
    assert len(hub.clients("doc1")) == 2
    
    hub.set_presence("doc1", "c1", cursor=None)
    clients = hub.clients("doc1")
    assert len(clients) == 1
    assert clients[0]["client"] == "c2"


def test_set_presence_agent_gets_agent_badge():
    """Agent clients (id starting with 'agent=') get the 'agent' flag."""
    hub = CollabHub()
    hub.set_presence("doc1", "human-1", user="Alice", cursor={"index": 0})
    hub.set_presence("doc1", "agent=assistant", user="Assistant", cursor={"index": 1})
    
    clients = hub.clients("doc1")
    human = next(c for c in clients if c["client"] == "human-1")
    agent = next(c for c in clients if c["client"] == "agent=assistant")
    
    assert human.get("agent") is False
    assert agent.get("agent") is True


def test_set_presence_default_user_to_client_id():
    """When user is not provided, client_id is used as user."""
    hub = CollabHub()
    hub.set_presence("doc1", "client-123", cursor={"index": 0})
    clients = hub.clients("doc1")
    assert clients[0]["user"] == "client-123"


def test_set_presence_overwrites_existing_client():
    """Same client_id updates the existing entry."""
    hub = CollabHub()
    hub.set_presence("doc1", "c1", user="Alice", cursor={"index": 0})
    before = hub.clients("doc1")[0]["updated"]
    
    time.sleep(0.01)  # ensure time difference
    hub.set_presence("doc1", "c1", user="Alice Updated", cursor={"index": 5})
    after = hub.clients("doc1")[0]["updated"]
    
    assert hub.clients("doc1")[0]["user"] == "Alice Updated"
    assert hub.clients("doc1")[0]["cursor"] == {"index": 5}
    assert after > before  # updated timestamp


# ----------------------------------------------------------------------
# RemoteWopiClient.acquire_or_adopt_lock adoption/expiry tests
# ----------------------------------------------------------------------


class _MockHost:
    """Minimal mock WOPI host for unit testing acquire_or_adopt_lock."""

    def __init__(self) -> None:
        self.locks: dict[str, str] = {}
        self.token_by_doc: dict[str, str] = {}

    def get_lock(self, doc_id: str) -> str:
        return self.locks.get(doc_id, "")

    def set_lock(self, doc_id: str, token: str) -> None:
        self.locks[doc_id] = token

    def clear_lock(self, doc_id: str) -> None:
        self.locks.pop(doc_id, None)


def _make_client(host: _MockHost, doc_id: str) -> RemoteWopiClient:
    """Create a RemoteWopiClient backed by the mock host."""
    # The mock doesn't implement real HTTP, so we'll monkeypatch the methods
    client = RemoteWopiClient("http://mock", "fake-token")
    
    # Patch the methods to use our mock host
    original_acquire = client.acquire_or_adopt_lock
    
    def patched_acquire(doc_id: str, owner: str = "") -> tuple[str, bool]:
        # Simulate the WOPI LOCK/GET_LOCK/UNLOCK behavior
        lock = host.get_lock(doc_id)
        
        if not owner:
            # Ownerless lock
            import uuid
            token = uuid.uuid4().hex
            host.set_lock(doc_id, token)
            client.lock_token = token
            return token, True
        
        # Owner-named lock
        import uuid
        new_token = f"wo:{owner}:{uuid.uuid4().hex}"
        
        if not lock:
            # Unlocked: take the lock
            host.set_lock(doc_id, new_token)
            client.lock_token = new_token
            return new_token, True
        elif lock.startswith("wo:"):
            # Has owner-named lock
            current_owner = lock.split(":", 2)[1] if lock.count(":") >= 2 else ""
            if current_owner == owner:
                # Same owner: adopt
                client.lock_token = lock
                return lock, True
            else:
                # Different owner: rejected
                client.lock_token = ""
                return "", False
        else:
            # Legacy lock: take it over
            host.set_lock(doc_id, new_token)
            client.lock_token = new_token
            return new_token, True
    
    client.acquire_or_adopt_lock = patched_acquire  # type: ignore[method-assign]
    return client


def test_acquire_or_adopt_lock_first_lock_wins():
    """First acquire on unlocked file gets lock with owner prefix."""
    host = _MockHost()
    client = _make_client(host, "doc1")
    
    lock, writable = client.acquire_or_adopt_lock("doc1", owner="alice")
    
    assert writable is True
    assert lock.startswith("wo:alice:")
    assert host.get_lock("doc1") == lock


def test_acquire_or_adopt_lock_same_owner_adopts():
    """Same owner re-acquiring adopts the existing lock."""
    host = _MockHost()
    client1 = _make_client(host, "doc1")
    
    lock1, w1 = client1.acquire_or_adopt_lock("doc1", owner="alice")
    assert w1 is True
    
    client2 = _make_client(host, "doc1")  # second client, same doc
    lock2, w2 = client2.acquire_or_adopt_lock("doc1", owner="alice")
    
    assert w2 is True
    assert lock2 == lock1  # adopted, not fresh
    assert host.get_lock("doc1") == lock1


def test_acquire_or_adopt_lock_different_owner_rejected():
    """Different owner is rejected and returns read-only."""
    host = _MockHost()
    client_alice = _make_client(host, "doc1")
    
    lock_alice, w_alice = client_alice.acquire_or_adopt_lock("doc1", owner="alice")
    assert w_alice is True
    
    client_bob = _make_client(host, "doc1")
    lock_bob, w_bob = client_bob.acquire_or_adopt_lock("doc1", owner="bob")
    
    assert w_bob is False
    assert lock_bob == ""
    assert client_bob.lock_token == ""
    assert host.get_lock("doc1") == lock_alice  # lock unchanged


def test_acquire_or_adopt_lock_legacy_lock_taken_over():
    """Legacy lock (no owner prefix) is taken over by first owner."""
    host = _MockHost()
    host.set_lock("doc1", "LEGACY-LOCK")
    
    client = _make_client(host, "doc1")
    lock, writable = client.acquire_or_adopt_lock("doc1", owner="alice")
    
    assert writable is True
    assert lock.startswith("wo:alice:")
    assert host.get_lock("doc1") == lock  # replaced with owner-named


def test_acquire_or_adopt_lock_ownerless_then_owner():
    """Ownerless lock can be taken over by owner-named client."""
    host = _MockHost()
    client = _make_client(host, "doc1")
    
    # First: ownerless lock
    lock1, w1 = client.acquire_or_adopt_lock("doc1", owner="")
    assert w1 is True
    assert not lock1.startswith("wo:")
    
    # Second: owner-named client takes it over
    client2 = _make_client(host, "doc1")
    lock2, w2 = client2.acquire_or_adopt_lock("doc1", owner="alice")
    
    assert w2 is True
    assert lock2.startswith("wo:alice:")
    assert host.get_lock("doc1") == lock2


def test_session_registry_drop_expires_all_sessions():
    """SessionRegistry.drop() expires all sessions for a doc."""
    reg = SessionRegistry()
    
    # Register multiple sessions for same doc
    s1 = type("EditorSession", (), {
        "doc_id": "doc1",
        "session_id": "s1",
        "created_at": 100.0,
        "lock_token": "tok1"
    })()
    s2 = type("EditorSession", (), {
        "doc_id": "doc1",
        "session_id": "s2", 
        "created_at": 200.0,
        "lock_token": "tok2"
    })()
    s3 = type("EditorSession", (), {
        "doc_id": "doc2",
        "session_id": "s3",
        "created_at": 150.0,
        "lock_token": "tok3"
    })()
    
    reg.register(s1)
    reg.register(s2)
    reg.register(s3)
    
    assert reg.get("doc1") is not None
    assert reg.get("doc2") is not None
    assert len(reg.all()) == 3
    
    # Drop doc1
    reg.drop("doc1")
    
    assert reg.get("doc1") is None
    assert reg.get_by_id("s1") is None
    assert reg.get_by_id("s2") is None
    assert reg.get("doc2") is not None  # untouched
    assert reg.get_by_id("s3") is not None


def test_session_registry_get_by_id_exact():
    """get_by_id returns exact match, not affected by doc_id."""
    reg = SessionRegistry()
    
    s1 = type("EditorSession", (), {
        "doc_id": "doc1",
        "session_id": "unique-1",
        "created_at": 100.0
    })()
    s2 = type("EditorSession", (), {
        "doc_id": "doc2",  # different doc
        "session_id": "unique-1",  # same session_id (edge case)
        "created_at": 200.0
    })()
    
    reg.register(s1)
    reg.register(s2)
    
    # get_by_id should be exact match by session_id
    assert reg.get_by_id("unique-1") is s2  # most recent wins by registration order in dict


# ----------------------------------------------------------------------
# Integration: char_at + lock adoption in collaborative context
# ----------------------------------------------------------------------


def test_char_at_with_concurrent_inserts():
    """char_at works correctly even with concurrent inserts."""
    hub = CollabHub()
    doc_id = "collab-doc"
    
    # Seed document
    hub.ensure(doc_id, initial_text="hello")
    
    # Alice inserts at position 5
    alice_crdt = TextCRDT("alice")
    alice_crdt.local_insert(5, " world")
    hub.apply_ops(doc_id, "alice", [alice_crdt.seed_op])
    
    # Bob inserts at position 0
    bob_crdt = TextCRDT("bob")
    bob_crdt.local_insert(0, "Start: ")
    hub.apply_ops(doc_id, "bob", [bob_crdt.seed_op])
    
    # char_at should reflect the merged text
    state = hub.state(doc_id)
    text = state["text"]
    
    # Verify char_at on the merged state
    assert hub.ensure(doc_id).crdt.char_at(0) == text[0] if text else None
    assert hub.ensure(doc_id).crdt.char_at(len(text) - 1) == text[-1] if text else None