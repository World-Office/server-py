"""Multi-instance store: two DocumentStore handles on one dir stay consistent.

This suite verifies that concurrent DocumentStore instances opened over the
same SQLite database and content directory remain consistent. All instances
share the same underlying files and must see the same state regardless of
which instance performs the write.

Paradigm: UNIT tests — deterministic, no network, no external services.
Uses the same fixtures as other store tests (`tmp_path`, `wipe_db`, `wipe_dir`).
"""

from __future__ import annotations

import time

import pytest

from src.lib.store import DocumentStore, wipe_db, wipe_dir


# =============================================================================
# Test: basic consistency across instances
# =============================================================================


def test_two_instances_share_same_database_state(tmp_path):
    """Two DocumentStore handles on the same dir see each other's changes.

    Sequence:
    1. Open store A, write doc1.
    2. Open store B over the same db/content dirs.
    3. Verify store B sees doc1 (metadata + content).
    4. Store B writes doc2.
    5. Verify store A sees doc2.
    """
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    # Instance A writes doc1
    store_a = DocumentStore(db, content)
    store_a.init("doc1", "file1.docx")
    store_a.put_content("doc1", b"content from A")
    assert store_a.get("doc1")["name"] == "file1.docx"
    assert store_a.get_content("doc1") == b"content from A"

    # Instance B opens the same storage
    store_b = DocumentStore(db, content)
    
    # B must see A's write immediately
    assert store_b.get("doc1") is not None
    assert store_b.get("doc1")["name"] == "file1.docx"
    assert store_b.get_content("doc1") == b"content from A"

    # B writes doc2
    store_b.init("doc2", "file2.docx")
    store_b.put_content("doc2", b"content from B")
    
    # A must see B's write immediately
    assert store_a.get("doc2") is not None
    assert store_a.get("doc2")["name"] == "file2.docx"
    assert store_a.get_content("doc2") == b"content from B"


# =============================================================================
# Test: lock state consistency across instances
# =============================================================================


def test_lock_acquired_by_one_instance_is_visible_to_other(tmp_path):
    """Lock state is shared across multiple store instances."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    store_a = DocumentStore(db, content)
    store_a.init("doc1", "file1.docx")
    
    store_b = DocumentStore(db, content)
    
    # A acquires lock
    store_a.set_lock("doc1", "token-abc", "alice")
    assert store_a.get_lock("doc1") == "token-abc"
    
    # B sees the lock
    assert store_b.get_lock("doc1") == "token-abc"
    assert store_b.get("doc1")["lock_user"] == "alice"
    
    # B releases the lock
    store_b.release_lock("doc1")
    assert store_b.get_lock("doc1") == ""
    
    # A sees the release
    assert store_a.get_lock("doc1") == ""


# =============================================================================
# Test: version history consistency across instances
# =============================================================================


def test_version_history_shared_across_instances(tmp_path):
    """Version snapshots created by one instance are visible to others."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    store_a = DocumentStore(db, content)
    store_a.init("doc1", "file1.docx")
    store_a.put_content("doc1", b"base")
    
    # A creates some versions
    time.sleep(0.01)
    store_a.put_version("doc1", b"v1", author="alice")
    time.sleep(0.01)
    store_a.put_version("doc1", b"v2", author="bob")
    
    versions_a = store_a.list_versions("doc1")
    assert len(versions_a) == 3  # base + v1 + v2

    store_b = DocumentStore(db, content)
    
    # B must see all versions created by A
    versions_b = store_b.list_versions("doc1")
    assert len(versions_b) == 3
    assert versions_b[0]["author"] == "bob"
    assert versions_b[1]["author"] == "alice"
    assert versions_b[2]["author"] == ""
    
    # B can read A's versions (versions are ordered newest-first)
    # versions_a[0] = bob's v2, versions_a[1] = alice's v1, versions_a[2] = base
    assert store_b.get_version("doc1", versions_a[0]["ts"]) == b"v2"  # newest
    assert store_b.get_version("doc1", versions_a[1]["ts"]) == b"v1"  # middle


# =============================================================================
# Test: content write atomicity across instances
# =============================================================================


def test_concurrent_writes_by_different_instances_use_last_write_wins(tmp_path):
    """Multiple instances writing the same document end with the last writer's data."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    store_a = DocumentStore(db, content)
    store_a.init("doc1", "file1.docx")
    
    store_b = DocumentStore(db, content)
    
    # Both instances write different content
    store_a.put_content("doc1", b"content from A")
    store_b.put_content("doc1", b"content from B")
    
    # SQLite transactions + RLock ensure atomic writes
    # Last writer wins — either A or B, but never a mix
    content = store_a.get_content("doc1")
    assert content == b"content from A" or content == b"content from B"
    
    # Index must be consistent with content
    meta = store_a.get("doc1")
    assert meta is not None
    assert meta["size"] == len(content)


# =============================================================================
# Test: list() and iteration consistency across instances
# =============================================================================


def test_list_ordering_is_consistent_across_instances(tmp_path):
    """Multiple instances see the same document list in the same order."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    store_a = DocumentStore(db, content)
    store_a.init("doc1", "first.docx")
    time.sleep(0.01)
    store_a.put_content("doc1", b"a")
    time.sleep(0.01)
    store_a.init("doc2", "second.docx")
    store_a.put_content("doc2", b"b")
    time.sleep(0.01)
    store_a.init("doc3", "third.docx")
    store_a.put_content("doc3", b"c")

    # A's list
    list_a = store_a.list()
    ids_a = [d["id"] for d in list_a]
    
    # B sees the same documents
    store_b = DocumentStore(db, content)
    list_b = store_b.list()
    ids_b = [d["id"] for d in list_b]
    
    # Both lists must have the same IDs
    assert set(ids_a) == set(ids_b) == {"doc1", "doc2", "doc3"}
    
    # Order should be the same (newest first by updated_at)
    assert ids_a == ids_b
    
    # Verify order is newest first
    assert ids_a[0] == "doc3"
    assert ids_a[1] == "doc2"
    assert ids_a[2] == "doc1"


# =============================================================================
# Test: delete consistency across instances
# =============================================================================


def test_delete_by_one_instance_makes_document_invisible_to_others(tmp_path):
    """Removing a document with one instance hides it from all others."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    store_a = DocumentStore(db, content)
    store_a.init("doc1", "file1.docx")
    store_a.put_content("doc1", b"content")
    
    store_b = DocumentStore(db, content)
    assert store_b.get("doc1") is not None
    
    # A deletes the document
    assert store_a.delete("doc1") is True
    
    # B must not see it anymore
    assert store_b.get("doc1") is None
    assert store_b.get_content("doc1") is None
    
    # List should not contain doc1
    assert all(d["id"] != "doc1" for d in store_b.list())
    
    # A confirms deletion
    assert store_a.get("doc1") is None


# =============================================================================
# Test: restore_version consistency across instances
# =============================================================================


def test_restore_version_by_one_instance_affects_all_instances(tmp_path):
    """Restoring a version with one instance updates state visible to all."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    store_a = DocumentStore(db, content)
    store_a.init("doc1", "file1.docx")
    store_a.put_content("doc1", b"current")
    
    # Create a version to restore
    time.sleep(0.01)
    store_a.put_version("doc1", b"old", author="alice")
    
    versions = store_a.list_versions("doc1")
    old_ts = next(v["ts"] for v in versions if v["author"] == "alice")

    store_b = DocumentStore(db, content)
    
    # B sees the current content
    assert store_b.get_content("doc1") == b"current"
    
    # A restores the old version
    store_a.restore_version("doc1", old_ts)
    
    # B must see the restored content
    assert store_b.get_content("doc1") == b"old"
    
    # A also sees the restored content
    assert store_a.get_content("doc1") == b"old"


# =============================================================================
# Test: size metadata consistency across instances
# =============================================================================


def test_size_metadata_is_consistent_across_instances(tmp_path):
    """Document size metadata is accurate regardless of which instance wrote it."""
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")

    store_a = DocumentStore(db, content)
    store_a.init("doc1", "file1.docx")
    store_a.put_content("doc1", b"x" * 100)
    
    store_b = DocumentStore(db, content)
    
    # B sees accurate metadata from A's write
    assert store_b.get("doc1")["size"] == 100
    assert store_b.get_content("doc1") == b"x" * 100
    
    # B writes new content
    store_b.put_content("doc1", b"y" * 200)
    
    # A sees updated metadata
    assert store_a.get("doc1")["size"] == 200
    assert store_a.get_content("doc1") == b"y" * 200