"""Tests for the SQLite document store."""

from __future__ import annotations

import pytest

from src.lib.store import DocumentStore, wipe_db, wipe_dir


@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "test.db")
    content = str(tmp_path / "content")
    s = DocumentStore(db, content)
    yield s
    wipe_db(db)
    wipe_dir(content)


def test_init_and_get(store):
    store.init("doc1", "hello.docx")
    row = store.get("doc1")
    assert row is not None
    assert row["name"] == "hello.docx"
    assert row["lock_token"] == ""


def test_init_idempotent(store):
    store.init("doc1", "a.docx")
    store.init("doc1", "b.docx")  # must not raise or overwrite
    assert store.get("doc1")["name"] == "a.docx"


def test_put_and_get_content(store):
    store.init("doc1", "hello.docx")
    store.put_content("doc1", b"the bytes")
    assert store.get_content("doc1") == b"the bytes"
    assert store.get("doc1")["size"] == 9
    assert store.has_content("doc1")


def test_get_unknown(store):
    assert store.get("nope") is None
    assert store.get_content("nope") is None


def test_lock_lifecycle(store):
    store.init("doc1", "x.docx")
    assert store.get_lock("doc1") == ""
    store.set_lock("doc1", "abc", "alice")
    assert store.get_lock("doc1") == "abc"
    store.release_lock("doc1")
    assert store.get_lock("doc1") == ""


def test_list_ordering(store):
    store.init("a", "a.docx")
    store.put_content("a", b"1")
    store.init("b", "b.docx")
    store.put_content("b", b"2")
    ids = [d["id"] for d in store.list()]
    assert ids == ["b", "a"]


def test_delete(store):
    store.init("doc1", "x.docx")
    store.put_content("doc1", b"data")
    assert store.delete("doc1") is True
    assert store.get("doc1") is None
    assert store.has_content("doc1") is False
    assert store.delete("doc1") is False
