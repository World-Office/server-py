"""SQLite document store.

Keeps document metadata, edit locks, and access tokens. Raw document
content is stored in `content_dir` (one file per id) while SQLite holds
the index and lock state — a storeroom ledger, not the warehouse.

Schema (single table `documents`):
    id          TEXT PRIMARY KEY   -- external id supplied by the WOPI host
    name        TEXT NOT NULL      -- base file name including extension
    size        INTEGER DEFAULT 0
    created_at  REAL               -- unix timestamp
    updated_at  REAL
    lock_token  TEXT               -- WOPI lock / "" when unlocked
    lock_user   TEXT               -- display name of the lock owner
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class DocumentStoreError(RuntimeError):
    """Raised on store-level failures (I/O, DB)."""


class DocumentStore:
    """SQLite-backed metadata index plus file-backed content."""

    def __init__(self, database: str, content_dir: str) -> None:
        self._db_path = Path(database)
        self._content_dir = Path(content_dir)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._content_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        # One shared SQLite connection is used by all threads (HTTP handlers
        # run concurrently), so every DB-touching method serializes through
        # this (reentrant) lock; RLock lets nested calls put_content →
        # put_version re-acquire safely.
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        try:
            self._init_schema()
        except sqlite3.Error as exc:
            # Storage is not a valid database (corrupted, truncated, or
            # overwritten). Fail with the store's OWN typed error instead of
            # leaking a raw sqlite traceback, and never silently initialise a
            # fresh empty store over it (that would hide data loss).
            self._conn.close()
            raise DocumentStoreError(
                f"storage unreadable or corrupt at {self._db_path!r}: {exc}"
            ) from exc

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id         TEXT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    size       INTEGER DEFAULT 0,
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0,
                    lock_token TEXT DEFAULT '',
                    lock_user  TEXT DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS versions (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id  TEXT NOT NULL,
                    ts      INTEGER NOT NULL,
                    author  TEXT DEFAULT '',
                    size    INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_versions_doc ON versions (doc_id, ts DESC)"
            )

    # ----- lifecycle ----------------------------------------------------

    def init(self, doc_id: str, name: str) -> dict[str, Any]:
        """Register a document in the index (idempotent), return its row."""
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO documents (id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (doc_id, name, now, now),
            )
        return self.get(doc_id)

    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Return the metadata row for a document, or None if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        """Return all registered documents, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM documents ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, doc_id: str) -> bool:
        """Remove a document and its content file. Returns True if removed."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        removed = cur.rowcount > 0
        if removed:
            self._delete_content(doc_id)
        return removed

    # ----- content ------------------------------------------------------

    def content_path(self, doc_id: str) -> Path:
        """Filesystem path where a document's bytes live."""
        return self._content_dir / f"{doc_id}.bin"

    def put_content(self, doc_id: str, data: bytes, author: str = "") -> None:
        """Write document bytes, update the index, and snapshot a version.

        Every content write records a byte-level snapshot (pruned to
        :attr:`MAX_VERSIONS`) so the document's history can be listed and
        restored. ``author`` is stored on the snapshot for attribution.

        Serialized through the store lock: concurrent saves must never corrupt
        the index/version ledger (last write wins, no partial state).
        """
        path = self.content_path(doc_id)
        with self._lock:
            path.write_bytes(data)
            now = time.time()
            with self._conn:
                self._conn.execute(
                    "UPDATE documents SET size = ?, updated_at = ? WHERE id = ?",
                    (len(data), now, doc_id),
                )
            self.put_version(doc_id, data, author=author)

    def get_content(self, doc_id: str) -> bytes | None:
        """Return document bytes, or None if content is missing."""
        path = self.content_path(doc_id)
        if not path.exists():
            return None
        return path.read_bytes()

    # ----- version history ---------------------------------------------

    #: Maximum number of snapshots kept per document (newest N retained).
    MAX_VERSIONS = 50

    _last_ts = 0  # monotonic across the store, so version order is stable

    def _versions_dir(self, doc_id: str):
        return self._content_dir / "versions" / doc_id

    def put_version(self, doc_id: str, data: bytes, author: str = "", ts: int | None = None) -> int:
        """Snapshot ``data`` as a new version of ``doc_id``; return its ts.

        Older snapshots beyond :attr:`MAX_VERSIONS` are pruned (files +
        index rows). The timestamp doubles as the snapshot's identity and
        its filename (``<ts>.bin`` in the per-doc versions directory).
        """
        if ts is None:
            # strictly increasing even for back-to-back writes in the same
            # millisecond, so ORDER BY ts mirrors real write order
            DocumentStore._last_ts = max(DocumentStore._last_ts, int(time.time() * 1000)) + 1
            ts = DocumentStore._last_ts
        vdir = self._versions_dir(doc_id)
        vdir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            (vdir / f"{ts}.bin").write_bytes(data)
            with self._conn:
                self._conn.execute(
                    "INSERT INTO versions (doc_id, ts, author, size) VALUES (?, ?, ?, ?)",
                    (doc_id, ts, author, len(data)),
                )
            # prune: keep the newest MAX_VERSIONS rows (drop older files too)
            rows = self._conn.execute(
                "SELECT ts FROM versions WHERE doc_id = ? ORDER BY ts DESC",
                (doc_id,),
            ).fetchall()
            drop = [r["ts"] for r in rows[self.MAX_VERSIONS :]] if len(rows) > self.MAX_VERSIONS else []
            if drop:
                with self._conn:
                    for old_ts in drop:
                        self._conn.execute(
                            "DELETE FROM versions WHERE doc_id = ? AND ts = ?", (doc_id, old_ts)
                        )
                for old_ts in drop:
                    f = vdir / f"{old_ts}.bin"
                    try:
                        f.unlink()
                    except OSError:
                        pass
        return ts

    def list_versions(self, doc_id: str) -> list[dict[str, object]]:
        """Return version metadata for a document, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, author, size FROM versions WHERE doc_id = ? ORDER BY ts DESC",
                (doc_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, doc_id: str, ts: int) -> bytes | None:
        """Return the snapshot bytes for a version, or None if unknown."""
        f = self._versions_dir(doc_id) / f"{ts}.bin"
        if not f.exists():
            return None
        return f.read_bytes()

    def restore_version(self, doc_id: str, ts: int) -> int:
        """Restore version ``ts`` as the document's current content.

        The pre-restore state is snapshotted first so the restore itself is
        undoable; the restore then becomes the newest version. Returns the
        timestamp of the new head version.
        """
        data = self.get_version(doc_id, ts)
        if data is None:
            raise DocumentStoreError(f"version {ts} not found for {doc_id}")
        current = self.get_content(doc_id)
        if current is None:
            raise DocumentStoreError(f"no current content for {doc_id}")
        # preserve the pre-restore state as a recoverable snapshot
        self.put_version(doc_id, current, author="")
        self.put_content(doc_id, data)
        versions = self.list_versions(doc_id)
        return versions[0]["ts"] if versions else ts

    def has_content(self, doc_id: str) -> bool:
        """True if a content file exists for the document."""
        return self.content_path(doc_id).exists()

    def _delete_content(self, doc_id: str) -> None:
        path = self.content_path(doc_id)
        if path.exists():
            path.unlink()

    # ----- locking ------------------------------------------------------

    def get_lock(self, doc_id: str) -> str:
        """Return the current lock token ('' when unlocked)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT lock_token FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return row["lock_token"] if row else ""

    def set_lock(self, doc_id: str, token: str, user: str = "") -> None:
        """Acquire/replace the lock on a document."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE documents SET lock_token = ?, lock_user = ? WHERE id = ?",
                (token, user, doc_id),
            )

    def release_lock(self, doc_id: str) -> None:
        """Clear the lock on a document."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE documents SET lock_token = '', lock_user = '' WHERE id = ?",
                (doc_id,),
            )


def wipe_db(db_path: str) -> None:
    """Remove the SQLite file (used by tests / reset)."""
    p = Path(db_path)
    if p.exists():
        p.unlink()
    # WAL/shm sidecars
    for suffix in ("-wal", "-shm"):
        side = Path(str(p) + suffix)
        if side.exists():
            side.unlink()


def wipe_dir(path: str) -> None:
    """Remove a content directory tree (used by tests / reset)."""
    p = Path(path)
    if p.exists():
        shutil.rmtree(p)


def ensure_dirs(database: str, content_dir: str) -> None:
    """Create parent directories for db and content files."""
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    Path(content_dir).mkdir(parents=True, exist_ok=True)
