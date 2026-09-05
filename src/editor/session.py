"""Editor session: bridges the local docserver with a remote WOPI host.

When OCIS launches our editor it passes an `access_token` (WopiContext
JWT). In that case the docserver becomes a WOPI **client**: it fetches
file bytes from the remote WOPI host (`{wopi_host}/wopi/files/{id}/...`)
using the given token, converts locally for editing, and forwards the
saved bytes back to the remote host with the token.

When no `wopi_host` is configured the docserver is a self-contained WOPI
**host** and all content lives in the local SQLite store.
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

from ..lib.crypto import decode_token


@dataclass
class EditorSession:
    """Immutable description of an active editing session."""

    doc_id: str
    name: str
    size: int
    version: str
    last_modified: int
    owner_id: str = "unknown"
    user_id: str = "anonymous"
    user_name: str = "Anonymous"
    remote_host: str | None = None
    access_token: str | None = None
    lock_token: str = ""
    read_only: bool = False
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    @property
    def in_client_mode(self) -> bool:
        """True when we are forwarding to a remote WOPI host (OCIS)."""
        return bool(self.remote_host and self.access_token)


class RemoteWopiClient:
    """Minimal HTTP client for a remote WOPI host (OCIS)."""

    def __init__(self, host: str, access_token: str, timeout: float = 30.0) -> None:
        self.host = host.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout
        self.lock_token = ""

    def _url(self, doc_id: str, action: str = "") -> str:
        base = f"{self.host}/wopi/files/{urllib.parse.quote(doc_id)}"
        if action:
            base += f"/{action}"
        # WOPI hosts expect the access token as a query parameter.
        sep = "?" if "?" not in base else "&"
        return f"{base}{sep}access_token={urllib.parse.quote(self.access_token, safe='')}"

    def get_contents(self, doc_id: str) -> bytes:
        """GET the raw file bytes from the remote host."""
        req = urllib.request.Request(self._url(doc_id, "contents"))
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def put_contents(self, doc_id: str, data: bytes) -> None:
        """POST new bytes back to the remote host (respecting our lock token).

        OpenCloud/OCIS wopiserver requires the `X-WOPI-Override: PUT`
        header on the `POST /wopi/files/{id}/contents` endpoint. The bare
        `POST /wopi/files/{id}` form (no `/contents`) returns HTTP 500 on
        OpenCloud 7.3.0, so we must include the `/contents` segment.
        """
        req = urllib.request.Request(
            self._url(doc_id, "contents"), data=data, method="POST"
        )
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("X-WOPI-Override", "PUT")
        if self.lock_token:
            req.add_header("X-WOPI-Lock", self.lock_token)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp.read()

    def acquire_or_adopt_lock(self, doc_id: str, owner: str = "") -> tuple[str, bool]:
        """Lock the remote file, or adopt/reject an existing lock by owner.

        OpenCloud/OCIS wopiserver refuses PutFile on an unlocked file
        (409 "Cannot PutFile on unlocked file"), so the docserver must take
        the WOPI lock at launch and present `X-WOPI-Lock` on every save.

        Locks are named `wo:{owner}:{uuid}` so a second session can decide:
        - same owner (same user re-opening, or an orphan lock left by the
          same user's crashed session)  -> adopt, stay writable;
        - different owner (another user currently editing) -> return
          writable=False so the session is served read-only;
        - owner unknown or legacy lock format -> adopt, stay writable
          (best effort; prevents breaking the happy path over a stale lock).

        Returns (lock_token, writable). "" if the host has no locking.
        """
        import uuid

        owner = owner or ""
        lock_token = f"wo:{owner}:{uuid.uuid4().hex}" if owner else uuid.uuid4().hex
        req = urllib.request.Request(self._url(doc_id), data=b"", method="POST")
        req.add_header("X-WOPI-Override", "LOCK")
        req.add_header("X-WOPI-Lock", lock_token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
            self.lock_token = lock_token
            return lock_token, True
        except urllib.error.HTTPError:
            # Already locked — inspect the current lock's owner.
            try:
                req2 = urllib.request.Request(self._url(doc_id), data=b"", method="POST")
                req2.add_header("X-WOPI-Override", "GET_LOCK")
                with urllib.request.urlopen(req2, timeout=self.timeout) as resp:
                    current = resp.headers.get("X-WOPI-Lock", "")
            except Exception:
                current = ""
            own_owner = current.split(":", 2)[1] if current.startswith("wo:") else ""
            if owner and own_owner and own_owner != owner:
                # Held by another user: serve read-only, do not steal the lock.
                self.lock_token = ""
                return "", False
            if current.startswith("wo:"):
                # Same owner: share the token (same user, multiple tabs)
                # so every one of their sessions keeps saving.
                self.lock_token = current
                return current, True
            # Legacy/unknown-format lock (pre-upgrade or crashed session):
            # take it over with an owner-named lock so LATER opens can
            # enforce cross-user read-only.
            try:
                requ = urllib.request.Request(self._url(doc_id), data=b"", method="POST")
                requ.add_header("X-WOPI-Override", "UNLOCK")
                requ.add_header("X-WOPI-Lock", current)
                urllib.request.urlopen(requ, timeout=self.timeout).read()
            except Exception:
                pass
            try:
                reql = urllib.request.Request(self._url(doc_id), data=b"", method="POST")
                reql.add_header("X-WOPI-Override", "LOCK")
                reql.add_header("X-WOPI-Lock", lock_token)
                urllib.request.urlopen(reql, timeout=self.timeout).read()
                self.lock_token = lock_token
                return lock_token, True
            except urllib.error.HTTPError:
                # Lock changed under us (race) — evaluate the new owner.
                try:
                    req3 = urllib.request.Request(self._url(doc_id), data=b"", method="POST")
                    req3.add_header("X-WOPI-Override", "GET_LOCK")
                    with urllib.request.urlopen(req3, timeout=self.timeout) as resp:
                        raced = resp.headers.get("X-WOPI-Lock", "")
                except Exception:
                    raced = ""
                if owner and raced.startswith("wo:") and raced.split(":", 2)[1] != owner:
                    self.lock_token = ""
                    return "", False
                self.lock_token = raced
                return raced, True

    def release_lock(self, doc_id: str) -> None:
        """Release the WOPI lock on the remote host (best effort)."""
        if not self.lock_token:
            return
        try:
            req = urllib.request.Request(self._url(doc_id), data=b"", method="POST")
            req.add_header("X-WOPI-Override", "UNLOCK")
            req.add_header("X-WOPI-Lock", self.lock_token)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
        except Exception:
            pass
        self.lock_token = ""

    def check_file_info(self, doc_id: str) -> dict:
        """GET CheckFileInfo from the remote host."""
        req = urllib.request.Request(self._url(doc_id))
        req.add_header("Authorization", f"Bearer {self.access_token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return _json(resp)


def _json(resp) -> dict:
    import json

    return json.loads(resp.read().decode("utf-8"))


class SessionRegistry:
    """In-memory registry of active editor sessions (ephemeral by design)."""

    def __init__(self) -> None:
        self._sessions: dict[str, EditorSession] = {}

    def register(self, session: EditorSession) -> None:
        # Key by unique session id so concurrent launches for the SAME file
        # (e.g. two users or two tabs) don't clobber one another's session.
        self._sessions[session.session_id] = session

    def get(self, doc_id: str) -> EditorSession | None:
        """Latest session for a doc id (backward-compatible shortcut)."""
        matches = [s for s in self._sessions.values() if s.doc_id == doc_id]
        return max(matches, key=lambda s: s.created_at) if matches else None

    def get_by_id(self, session_id: str) -> EditorSession | None:
        return self._sessions.get(session_id)

    def drop(self, doc_id: str) -> None:
        for sid in [k for k, s in self._sessions.items() if s.doc_id == doc_id]:
            self._sessions.pop(sid, None)

    def all(self) -> list[EditorSession]:
        return sorted(self._sessions.values(), key=lambda s: s.created_at)


def session_from_token(token: str, secret: str) -> EditorSession | None:
    """Create an EditorSession from a signed WOPI token (client mode).

    Returns None when the token is absent/invalid — the caller then falls
    back to local host mode.
    """
    if not token:
        return None
    try:
        claims = decode_token(secret, token)
    except Exception:
        return None
    return EditorSession(
        doc_id=claims.get("file_id") or claims.get("sub") or "",
        name=claims.get("file_name", "document.docx"),
        size=int(claims.get("file_size", 0)),
        version=claims.get("version", "1"),
        last_modified=int(claims.get("iat", time.time())),
        owner_id=claims.get("user_id", "unknown"),
        user_id=claims.get("user_id", "anonymous"),
        user_name=claims.get("user_name", "Anonymous"),
    )
