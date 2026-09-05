"""Route/API fuzzing — the whole HTTP surface must never crash.

State-of-the-art *server fuzzing*: Hypothesis drives every documented and
mutable endpoint with arbitrary-but-encoded document ids (unicode,
traversal attempts, over-long, control characters, percent-encoding),
random query strings, random bytes and random JSON bodies.

Invariants for every single request the machine can generate:

* status is never 5xx — a server-side defect (uncaught exception) must be
  impossible to trigger from the HTTP boundary;
* the body never leaks a Python traceback / FastAPI error page
  (``Traceback``, ``Internal Server Error``);
* JSON endpoints return parseable JSON.

This complements the hand-written security tests in ``test_wopi.py`` by
generating far more inputs than a human would ever enumerate — including
cross-format and malformed ones the author did not think of.
"""

from __future__ import annotations

import json
import urllib.parse
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.config import Config
from src.editor.collab import reset_hub
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router

# Curated hostile document ids: traversal, separators, control chars,
# percent-encoding, unicode, over-long, injection-ish.
_HOSTILE_IDS = [
    "", "..", ".", "../secret", "..\\..\\secret", "a/b", "a\\b", "%2e%2e",
    "%2e%2e%2fsecret", "..%2Fsecret", "x\x00y", "αβγ-δοκιμή", "a" * 300,
    "doc id with spaces", "<script>alert(1)</script>", '"quoted"',
    "ünïcödé", "a" * 5, "normal-id", "42",
]

_DOC_ID = st.one_of(
    st.sampled_from(_HOSTILE_IDS),
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=14),
)

# Arbitrary request bodies: raw bytes, plain text, or a random JSON tree.
_BODY = st.one_of(
    st.binary(min_size=0, max_size=256),
    st.text(min_size=0, max_size=128),
    st.dictionaries(
        st.text(min_size=0, max_size=8),
        st.one_of(st.text(min_size=0, max_size=16), st.integers(), st.booleans()),
        max_size=4,
    ),
    st.none(),
)

# Everything with a {id} path param. (Excluded: /collab/stream — it is a
# never-ending SSE endpoint by design; /api/upload — multipart-only.)
_ROUTES: list[tuple[str, str]] = [
    # WOPI host surface
    ("GET", "/wopi/files/{id}"),
    ("GET", "/wopi/files/{id}/contents"),
    ("POST", "/wopi/files/{id}/contents"),
    ("POST", "/wopi/files/{id}/lock"),
    ("POST", "/wopi/files/{id}/unlock"),
    ("POST", "/wopi/files/{id}/refreshlock"),
    ("POST", "/wopi/files/{id}/getlock"),
    # editor document API
    ("GET", "/api/documents/{id}"),
    ("GET", "/api/documents/{id}/html"),
    ("GET", "/api/documents/{id}/contents"),
    ("PUT", "/api/documents/{id}/contents"),
    ("POST", "/api/documents/{id}/contents"),
    ("POST", "/api/documents/{id}/save"),
    ("POST", "/api/documents/{id}/export"),
    ("GET", "/api/documents/{id}/versions"),
    ("POST", "/api/documents/{id}/versions/123/restore"),
    # collaboration API
    ("GET", "/api/documents/{id}/collab/state"),
    ("GET", "/api/documents/{id}/collab/ops"),
    ("POST", "/api/documents/{id}/collab/ops"),
    ("POST", "/api/documents/{id}/collab/sync"),
    ("POST", "/api/documents/{id}/collab/resync"),
    ("POST", "/api/documents/{id}/collab/presence"),
    ("GET", "/api/documents/{id}/collab/presence"),
    # editor page
    ("GET", "/editor/{id}"),
]


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


def _client_factory(tmp_path):
    reset_hub()
    app, store = _make_app(tmp_path)
    client = TestClient(app)
    client.__enter__()
    client.test_store = store  # type: ignore[attr-defined]
    return client, str(tmp_path / "t.db"), str(tmp_path / "content")


def _enc(doc_id: str) -> str:
    """Percent-encode a fuzzed doc id so the server receives exactly these
    octets (traversal shapes arrive as the same separators the hand-written
    security tests exercise)."""
    return urllib.parse.quote(doc_id, safe="")


def _call(client, method: str, path: str, body):
    if body is None:
        return client.request(method, path)
    if isinstance(body, dict):
        return client.request(method, path, json=body)
    if isinstance(body, bytes):
        return client.request(method, path, content=body)
    return client.request(method, path, content=str(body).encode("utf-8"))


@given(doc_id=_DOC_ID, body=_BODY)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_http_surface_never_5xxs(tmp_path, doc_id, body):
    client, db, content = _client_factory(tmp_path)
    try:
        for method, pattern in _ROUTES:
            path = pattern.replace("{id}", _enc(doc_id)).replace("{ts}", "123")
            res = _call(client, method, path, body)
            assert res.status_code < 500, (
                f"{method} {path} -> {res.status_code} with {body!r}:\n{res.text[:400]}"
            )
            assert "Traceback" not in res.text, f"traceback leaked on {method} {path}"
            assert "Internal Server Error" not in res.text, (
                f"error page leaked on {method} {path}"
            )
            # every JSON endpoint must return parseable JSON (binary
            # endpoints like GetFile/export are excluded by content-type)
            ct = res.headers.get("content-type", "")
            if "application/json" in ct:
                json.loads(res.text)  # must not raise
    finally:
        client.__exit__(None, None, None)
        wipe_db(db)
        wipe_dir(content)
        reset_hub()


@given(params=st.dictionaries(
    st.text(min_size=0, max_size=10),
    st.one_of(st.text(min_size=0, max_size=12), st.integers(min_value=-5, max_value=5)),
    max_size=4,
))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_stateless_endpoints_survive_parameter_fuzz(tmp_path, params):
    """Discovery, document list, and the new-document factory must never
    crash no matter what query parameters arrive."""
    client, db, content = _client_factory(tmp_path)
    try:
        for path in ("/hosting/discovery", "/api/documents", "/health"):
            res = client.get(path, params=params)
            assert res.status_code < 500, f"GET {path} {params} -> {res.status_code}"
        res = client.post("/api/documents/new", params=params)
        assert res.status_code < 500, f"POST /api/documents/new {params} -> {res.status_code}"
    finally:
        client.__exit__(None, None, None)
        wipe_db(db)
        wipe_dir(content)
        reset_hub()
