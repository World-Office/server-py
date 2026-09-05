"""Model-based and adversarial tests for the WOPI protocol boundary.

Paradigms:

* **Model-based lock contract** — a Hypothesis state machine drives real
  HTTP Lock/Unlock/RefreshLock/GetLock/PutFile calls against the FastAPI
  app while mirroring the server's lock table in a reference model. After
  EVERY step the server's observable lock state must equal the model, and
  every response must match the WOPI semantics the reference encodes:
  first-writer-wins locking, token echo on conflict, PutFile honouring the
  lock. PutFile additionally verifies the bytes actually persisted and
  returned by GetFile. The machine explores state sequences (e.g. refresh-
  on-unlocked-then-foreign-lock) no hand-written test reaches.

* **Adversarial token property tests** — no single-byte mutation of a valid
  JWT's header, payload or signature may ever decode (tamper-evidence as a
  property); arbitrary opaque strings (fuzz) must never authenticate; and
  the classic bypass battery — alg:none, wrong secret, expired, truncated
  signature, appended garbage — is rejected one by one, both at the crypto
  boundary and through the legacy launch path.

Everything runs against the real FastAPI app via TestClient — no mocks.
"""

from __future__ import annotations

import base64
import json as _json
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry, session_from_token
from src.lib.crypto import decode_token, encode_token
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router

SECRET = "0123456789abcdef0123456789abcdef"  # 32 bytes, RFC 7518 minimum
OTHER_SECRET = "fedcba9876543210fedcba9876543210"

LOCK_TOKENS = st.sampled_from(["L1", "L2", "T-a", "T-b", "zzz", ""])
DOC_IDS = st.sampled_from(["d1", "d2", "d3"])

# ---------------------------------------------------------------------------
# Shared app builder (kept in sync with tests/test_wopi.py::_make_app)
# ---------------------------------------------------------------------------


def _make_app(db: str, content: str) -> tuple[FastAPI, DocumentStore]:
    store = DocumentStore(db, content)
    cfg = Config(database=db, content_dir=content, jwt_secret=SECRET)

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


def _valid_token() -> str:
    return encode_token(SECRET, {"file_id": "doc1", "user_id": "alice"}, ttl=3600)


# ---------------------------------------------------------------------------
# 1. Model-based WOPI lock lifecycle
# ---------------------------------------------------------------------------


@settings(max_examples=40, stateful_step_count=100, deadline=None)
class WopiLockStateMachine(RuleBasedStateMachine):
    """HTTP-level lock lifecycle vs a reference lock table."""

    def __init__(self) -> None:
        super().__init__()
        self._tmp = Path(tempfile.mkdtemp(prefix="wo-lock-"))
        db = str(self._tmp / "lock.db")
        content = str(self._tmp / "content")
        app, store = _make_app(db, content)
        for i, doc_id in enumerate(("d1", "d2", "d3")):
            store.init(doc_id, f"{doc_id}.docx")
            store.put_content(doc_id, f"seed-{i}".encode())
        self.client = TestClient(app)
        self.client.__enter__()
        self.store = store
        self.db, self.content = db, content
        self.docs = ("d1", "d2", "d3")
        self.locks: dict[str, str] = {d: "" for d in self.docs}  # "" == unlocked
        self.bodies: dict[str, bytes] = {
            d: f"seed-{i}".encode() for i, d in enumerate(self.docs)
        }

    def _post(self, path: str, token: str, *, body: bytes | None = None):
        headers = {"X-WOPI-Lock": token}
        if body is not None:
            return self.client.post(path, content=body, headers=headers)
        return self.client.post(path, headers=headers)

    @invariant()
    def server_lock_state_matches_reference(self) -> None:
        for doc in self.docs:
            # GetLock is a POST-only WOPI endpoint; any header works here.
            res = self.client.post(f"/wopi/files/{doc}/getlock")
            assert res.status_code == 200, doc
            expected = self.locks[doc] or " "  # GetLock echoes " " when unlocked
            assert res.headers.get("X-WOPI-Lock") == expected, (
                f"{doc}: server lock {res.headers.get('X-WOPI-Lock')!r} "
                f"!= model lock {self.locks[doc]!r}"
            )

    @rule(doc=DOC_IDS, token=LOCK_TOKENS)
    def lock(self, doc: str, token: str) -> None:
        cur = self.locks[doc]
        res = self._post(f"/wopi/files/{doc}/lock", token)
        if token == "":
            assert res.status_code == 400, "empty lock tokens must be rejected"
        elif cur == "":
            assert res.status_code == 200
            self.locks[doc] = token
        elif cur == token:
            assert res.status_code == 200  # same-token re-lock = refresh
        else:
            assert res.status_code == 409  # first-writer-wins
            assert res.headers.get("X-WOPI-Lock") == cur, "loser must see winner token"

    @rule(doc=DOC_IDS, token=LOCK_TOKENS)
    def unlock(self, doc: str, token: str) -> None:
        cur = self.locks[doc]
        res = self._post(f"/wopi/files/{doc}/unlock", token)
        if cur == "":
            assert res.status_code == 200  # unlocking an unlocked file is a no-op
        elif cur == token:
            assert res.status_code == 200
            self.locks[doc] = ""
        else:
            assert res.status_code == 409
            assert res.headers.get("X-WOPI-Lock") == cur

    @rule(doc=DOC_IDS, token=LOCK_TOKENS)
    def refresh(self, doc: str, token: str) -> None:
        cur = self.locks[doc]
        res = self._post(f"/wopi/files/{doc}/refreshlock", token)
        if token == "" and cur == "":
            assert res.status_code == 200  # no-op on an unlocked file
        elif cur == "":
            # Pinned server contract: a refresh on an unlocked file acquires it.
            assert res.status_code == 200
            self.locks[doc] = token
        elif cur == token:
            assert res.status_code == 200
        else:
            assert res.status_code == 409
            assert res.headers.get("X-WOPI-Lock") == cur

    @rule(doc=DOC_IDS, token=LOCK_TOKENS, body=st.binary(min_size=0, max_size=64))
    def put_file(self, doc: str, token: str, body: bytes) -> None:
        cur = self.locks[doc]
        res = self._post(f"/wopi/files/{doc}/contents", token, body=body)
        if cur and token != cur:
            assert res.status_code == 409, "PutFile must honour the lock"
            assert res.headers.get("X-WOPI-Lock") == cur
        else:
            assert res.status_code == 200
            self.bodies[doc] = body
            # Bytes must be persisted exactly and served back by GetFile.
            assert self.store.get_content(doc) == body
            got = self.client.get(f"/wopi/files/{doc}/contents")
            assert got.status_code == 200
            assert got.content == body

    def teardown(self) -> None:
        try:
            self.client.__exit__(None, None, None)
        finally:
            wipe_db(self.db)
            wipe_dir(self.content)
            shutil.rmtree(self._tmp, ignore_errors=True)


TestWopiLockStateMachine = WopiLockStateMachine.TestCase

# ---------------------------------------------------------------------------
# 2. Adversarial token testing
# ---------------------------------------------------------------------------


@given(index=st.integers(min_value=0, max_value=600), replacement=st.integers(0, 255))
@settings(max_examples=300, deadline=None)
def test_no_single_byte_tamper_of_a_valid_token_is_accepted(index: int, replacement: int):
    """Cryptographic tamper-evidence as a property: flipping ANY single byte
    of the header, payload or signature of a valid token must break signature
    verification — every byte, not just the ones a hand-written test thinks
    of."""
    token = _valid_token()
    raw = bytearray(token.encode("ascii"))
    if index >= len(raw) or raw[index] == replacement:
        return  # nothing to flip (or the byte was already that value)
    raw[index] = replacement
    tampered = bytes(raw).decode("ascii", errors="replace")
    with pytest.raises(Exception):
        decode_token(SECRET, tampered)


@given(junk=st.text())
@settings(max_examples=300, deadline=None)
def test_arbitrary_strings_never_authenticate(junk: str):
    """Fuzz: whatever opaque string arrives as a token, it must be rejected."""
    with pytest.raises(Exception):
        decode_token(SECRET, junk)


def test_alg_none_token_rejected():
    header = base64.urlsafe_b64encode(
        _json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        _json.dumps({"file_id": "doc1"}).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(Exception):
        decode_token(SECRET, f"{header}.{payload}.")


def test_wrong_secret_rejected():
    token = encode_token(OTHER_SECRET, {"file_id": "doc1"}, ttl=60)
    with pytest.raises(Exception):
        decode_token(SECRET, token)


def test_expired_token_rejected():
    token = encode_token(SECRET, {"file_id": "doc1"}, ttl=1, now=time.time() - 3600)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(SECRET, token)


def test_truncated_signature_rejected():
    with pytest.raises(Exception):
        decode_token(SECRET, _valid_token()[:-1])


def test_appended_garbage_rejected():
    with pytest.raises(Exception):
        decode_token(SECRET, _valid_token() + "x")


def test_legacy_launch_rejects_garbage_accepts_valid():
    """session_from_token is the legacy client-mode gate: garbage must yield
    None (fall back to host mode), a real signed token must yield a session."""
    assert session_from_token("", SECRET) is None
    assert session_from_token("not-a-jwt", SECRET) is None
    assert session_from_token("a.b.c", SECRET) is None
    good = encode_token(SECRET, {"file_id": "doc9", "user_id": "alice"}, ttl=60)
    session = session_from_token(good, SECRET)
    assert session is not None
    assert session.doc_id == "doc9"
    assert session.user_id == "alice"
