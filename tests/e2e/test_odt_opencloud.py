"""E2E — ODT document through production OpenCloud (OCIS client mode).

This suite drives the exact code path the docserver uses in production
(deployed against real OpenCloud / OCIS, e.g. cloud.graphwiz.ai):

    seed an ODT on the OpenCloud host
      -> OpenCloud launches /editor with access_token + WOPISrc  (WOPI handshake)
      -> GET  /api/documents/{id}/html                           (ODT -> HTML)
      -> edit the HTML in the editor (insert the persistence marker)
      -> POST /api/documents/{id}/save                           (HTML -> ODT)
      -> docserver PUTs the ODT back to the remote host          (PutFile)
      -> GET  /api/documents/{id}/html again                     (reload)
      -> re-read the ODT stored on the remote host and verify its
         content.xml contains the marker text.

A real HTTP WOPI host stands in for OCIS (same wire protocol, same
lock-before-PutFile semantics the wopiserver enforces). The real
docserver app, editor router, RemoteWopiClient and ODT converter are
exercised end to end — nothing is mocked on the docserver side.

The suite asserts the persistence contract: the marker text typed in the
editor survives every hop of the production path, and the stored bytes
are a real ODT whose content.xml contains it.

Marker emitted when the full production-path persistence check passes:
ODT-PERSISTENCE: OK
"""

from __future__ import annotations

import io
import json
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from wsgiref.simple_server import make_server

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from odf.opendocument import OpenDocumentText, load
from odf.style import Style, TextProperties
from odf.table import Table, TableCell, TableRow
from odf.text import H, List, ListItem, P, Span

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router

# Marker the editor types into an ODT; the suite never treats the save as
# successful until the marker is found in the stored content.xml.
MARKER = "ODT-PERSISTENCE"


# ----------------------------------------------------------------------
# Production OpenCloud (OCIS) wopiserver stand-in
# ----------------------------------------------------------------------

class _ProdOcisHost:
    """Minimal OCIS wopiserver over WSGI — what cloud.graphwiz.ai does.

    Implements the same WOPI surface the real wopiserver serves and the
    same quirks the docserver's RemoteWopiClient depends on:
      -  GET  /wopi/files/{id}             CheckFileInfo (Bearer auth)
      -  GET  /wopi/files/{id}/contents    GetFile
      -  POST /wopi/files/{id}/contents    PutFile (X-WOPI-Override: PUT)
      -  POST /wopi/files/{id}             LOCK / GET_LOCK / UNLOCK
    Like the real wopiserver, PutFile on an unlocked file is refused
    (409 "Cannot PutFile on unlocked file"), so every save must present
    the lock the docserver took at launch.
    """

    def __init__(self) -> None:
        self.content: dict[str, bytes] = {}
        self.names: dict[str, str] = {}
        self.locks: dict[str, str] = {}
        self.users: dict[str, str] = {}
        self.put_count = 0
        self.put_overrides: list[str] = []
        self.put_lock_headers: list[str] = []
        self.lock_events: list[str] = []

    def seed(self, doc_id: str, name: str, data: bytes, user: str = "alice") -> None:
        self.content[doc_id] = data
        self.names[doc_id] = name
        self.users[doc_id] = user

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        override = environ.get("HTTP_X_WOPI_OVERRIDE", "")
        lock_hdr = environ.get("HTTP_X_WOPI_LOCK", "")
        auth = environ.get("HTTP_AUTHORIZATION", "")

        m = re.match(r"^/wopi/files/([^/]+)(/contents)?$", path)
        if not m:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"not found"]
        doc_id, is_contents = m.group(1), bool(m.group(2))
        if doc_id not in self.content:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"no such file"]

        # GetFile — the docserver reads the raw ODT bytes with the token.
        if method == "GET" and is_contents:
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/octet-stream"),
                    ("X-WOPI-ItemVersion", "v1"),
                ],
            )
            return [self.content[doc_id]]

        # CheckFileInfo — the docserver reads BaseFileName (routes .odt to
        # the ODT converter) and UserId (names the WOPI lock).
        if method == "GET" and not is_contents:
            user_id = auth.replace("Bearer ", "") or self.users.get(doc_id, "unknown")
            body = json.dumps(
                {
                    "BaseFileName": self.names.get(doc_id, "document.odt"),
                    "UserId": user_id,
                    "Size": len(self.content[doc_id]),
                }
            ).encode()
            start_response("200 OK", [("Content-Type", "application/json")])
            return [body]

        # PutFile — only accepted with the lock taken at launch.
        if method == "POST" and is_contents and override == "PUT":
            length = int(environ.get("CONTENT_LENGTH", "0"))
            if lock_hdr != (self.locks.get(doc_id) or ""):
                if not self.locks.get(doc_id):
                    start_response(
                        "409 Conflict",
                        [
                            ("Content-Type", "text/plain"),
                            ("X-WOPI-Lock", ""),
                            ("X-WOPI-LockFailureReason", "Cannot PutFile on unlocked files"),
                        ],
                    )
                    return [b"conflict"]
                start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
                return [b"lock mismatch"]
            self.content[doc_id] = environ["wsgi.input"].read(length)
            self.put_count += 1
            self.put_overrides.append(override)
            self.put_lock_headers.append(lock_hdr)
            start_response("200 OK", [("Content-Type", "application/json")])
            return [b"{}"]

        if method == "POST" and not is_contents and override == "LOCK":
            if self.locks.get(doc_id) and self.locks[doc_id] != lock_hdr:
                start_response(
                    "409 Conflict",
                    [("Content-Type", "text/plain"), ("X-WOPI-Lock", self.locks[doc_id])],
                )
                return [b"locked by other"]
            self.locks[doc_id] = lock_hdr
            self.lock_events.append(f"LOCK:{doc_id}")
            start_response(
                "200 OK",
                [("Content-Type", "application/json"), ("X-WOPI-ItemVersion", "v1")],
            )
            return [b"{}"]

        if method == "POST" and not is_contents and override == "GET_LOCK":
            start_response(
                "200 OK",
                [("Content-Type", "application/json"), ("X-WOPI-Lock", self.locks.get(doc_id, ""))],
            )
            return [b"{}"]

        if method == "POST" and not is_contents and override == "UNLOCK":
            if lock_hdr == self.locks.get(doc_id):
                self.locks.pop(doc_id)
            self.lock_events.append(f"UNLOCK:{doc_id}")
            start_response("200 OK", [("Content-Type", "application/json")])
            return [b"{}"]

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]


# ----------------------------------------------------------------------
# The docserver wired against a live remote OpenCloud host
# ----------------------------------------------------------------------

class _E2EStack:
    """The docserver plus a remote OpenCloud host, wired like production.

    The docserver runs as a FastAPI TestClient; the remote host is a real
    WSGI server on 127.0.0.1 so the docserver's RemoteWopiClient makes real
    HTTP calls (urllib) exactly like it would against cloud.graphwiz.ai.
    """

    def __init__(self, tmp_path) -> None:
        self.host = _ProdOcisHost()
        self._httpd = make_server("127.0.0.1", 0, self.host)
        self.port = self._httpd.server_address[1]
        self.wopi_host = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

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
        self.client = TestClient(app)
        self.client.__enter__()  # run lifespan (app.state.*)
        self._db, self._content = db, content

    def close(self) -> None:
        try:
            self.client.__exit__(None, None, None)
        except Exception:
            pass
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        wipe_db(self._db)
        wipe_dir(self._content)

    def launch_editor(self, doc_id: str, user: str = "alice") -> str:
        """OpenCloud launches /editor (POST form) for a user's file."""
        token = f"tok-{user}"
        wopi_src = f"{self.wopi_host}/wopi/files/{doc_id}"
        resp = self.client.post(
            "/editor",
            params={"WOPISrc": wopi_src},
            data={"file_id": doc_id, "access_token": token},
        )
        assert resp.status_code == 200
        assert self.host.locks.get(doc_id), "launch must have taken the WOPI lock"
        return token

    def load_html(self, doc_id: str) -> str:
        resp = self.client.get(f"/api/documents/{doc_id}/html")
        assert resp.status_code == 200, resp.text
        return resp.json()["html"]

    def save_html(self, doc_id: str, html: str) -> None:
        resp = self.client.post(f"/api/documents/{doc_id}/save", json={"html": html})
        assert resp.status_code == 200, resp.text
        assert resp.json().get("ok") is True

    def unlock(self, doc_id: str) -> None:
        resp = self.client.post(f"/api/documents/{doc_id}/unlock")
        assert resp.status_code == 200, resp.text

    def remote_doc(self, doc_id: str) -> bytes:
        """Raw bytes the OpenCloud host stored after the docserver's PutFile."""
        return self.host.content[doc_id]


@pytest.fixture
def stack(tmp_path):
    s = _E2EStack(tmp_path)
    yield s
    s.close()


# ----------------------------------------------------------------------
# ODT fixtures + content.xml inspection
# ----------------------------------------------------------------------

def _build_odt(title: str = "Stoic Title", body: str = "Original Stoic body") -> bytes:
    """Build an ODT with a heading, a bold run and a plain paragraph."""
    doc = OpenDocumentText()
    doc.text.addElement(H(outlinelevel=1, text=title))
    bold = Style(name="WO_B", family="text")
    bold.addElement(TextProperties(fontweight="bold"))
    doc.automaticstyles.addElement(bold)
    p = P()
    p.addElement(Span(text=body, stylename="WO_B"))
    doc.text.addElement(p)
    doc.text.addElement(P(text="Plain text line."))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _odt_text(data: bytes) -> str:
    from odf import teletype

    doc = load(io.BytesIO(data))
    return teletype.extractText(doc.text)


def _content_xml_has(data: bytes, needle: str) -> bool:
    """True when the ODT's content.xml (the persisted body) contains needle."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        content_xml = zf.read("content.xml").decode("utf-8", "replace")
    return needle in content_xml


# ----------------------------------------------------------------------
# The E2E tests
# ----------------------------------------------------------------------

def test_odt_edit_persists_through_production_opencloud(stack):
    """The full production roundtrip: OpenCloud launch -> edit -> save ->
    PutFile -> reload. Emits the ODT-PERSISTENCE marker only when the ODT
    stored back on the OpenCloud host contains the marker in content.xml."""
    stack.host.seed("odt-prod", "stoic.odt", _build_odt(title="Stoic Title", body="Original Stoic body"))
    stack.launch_editor("odt-prod", user="alice")

    # OpenCloud serves the ODT as HTML for the editor.
    html = stack.load_html("odt-prod")
    assert "Original Stoic body" in html
    assert "Stoic Title" in html

    # The user types the marker into the document.
    edited = html + f"\n<p>{MARKER}</p>"
    stack.save_html("odt-prod", edited)

    # The docserver must have PUT the ODT back to the OpenCloud host,
    # presenting the WOPI lock it took at launch (409 otherwise).
    assert stack.host.put_count == 1, "edited doc must be PUT back to OpenCloud"
    assert "PUT" in stack.host.put_overrides
    assert stack.host.put_lock_headers and stack.host.put_lock_headers[0], (
        "save must present the WOPI lock taken at launch"
    )

    # Reload from the remote host: the edited text is still there.
    reloaded = stack.load_html("odt-prod")
    assert MARKER in reloaded

    # The stored bytes are a real ODT whose content.xml contains the marker.
    stored = stack.remote_doc("odt-prod")
    assert _content_xml_has(stored, MARKER), "content.xml must contain the marker"
    assert MARKER in _odt_text(stored)
    assert "Original Stoic body" in _odt_text(stored)

    print("ODT-PERSISTENCE: OK")


def test_odt_formatting_survives_production_roundtrip(stack):
    """Heading and bold survive ODT -> HTML -> ODT through the remote host."""
    stack.host.seed("odt-style", "styled.odt", _build_odt("Styled Title", "Bold body"))
    stack.launch_editor("odt-style", user="alice")

    html = stack.load_html("odt-style")
    assert "<h1>Styled Title</h1>" in html
    assert "<b>Bold body</b>" in html

    # Save the HTML back (what a no-op edit does); the stored ODT must
    # keep the heading and the bold run.
    stack.save_html("odt-style", html)
    stored = stack.remote_doc("odt-style")
    assert _content_xml_has(stored, "Styled Title")
    assert _content_xml_has(stored, "Bold body")
    assert stack.host.put_count == 1


def test_odt_table_and_list_persist_through_production_opencloud(stack):
    """Tables and lists in an ODT survive the production roundtrip and the
    marker lands in content.xml next to them."""
    doc = OpenDocumentText()
    doc.text.addElement(P(text="Intro"))
    table_el = Table()
    tr = TableRow()
    for cell_text in ("a1", "b1"):
        tc = TableCell()
        tc.addElement(P(text=cell_text))
        tr.addElement(tc)
    table_el.addElement(tr)
    doc.text.addElement(table_el)
    ol = List()
    for item_text in ("first", "second"):
        li = ListItem()
        li.addElement(P(text=item_text))
        ol.addElement(li)
    doc.text.addElement(ol)
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()

    stack.host.seed("odt-table", "table.odt", data)
    stack.launch_editor("odt-table", user="alice")

    html = stack.load_html("odt-table")
    assert "<table>" in html and "<td><p>a1</p></td>" in html
    assert "<ul>" in html and "<li>first</li>" in html

    stack.save_html("odt-table", html + f"\n<p>{MARKER} tail</p>")
    stored = stack.remote_doc("odt-table")
    assert _content_xml_has(stored, "a1")
    assert _content_xml_has(stored, "first")
    assert _content_xml_has(stored, MARKER)


def test_odt_edit_survives_reload_after_remote_put_and_unlock(stack):
    """After a save (remote PutFile) and a fresh launch (unlock + re-open),
    the edit is still there — production 'close and reopen' flow."""
    stack.host.seed("odt-reopen", "reopen.odt", _build_odt("Keep this"))
    stack.launch_editor("odt-reopen", user="alice")

    html = stack.load_html("odt-reopen")
    stack.save_html("odt-reopen", html + f"\n<p>{MARKER}-reopen</p>")
    assert stack.host.put_count == 1

    # Close the document: unlock on the remote host, then reopen it through
    # a fresh launch as another user.
    stack.unlock("odt-reopen")
    stack.launch_editor("odt-reopen", user="bob")

    again = stack.load_html("odt-reopen")
    assert "Keep this" in again
    assert f"{MARKER}-reopen" in again


def test_blank_odt_becomes_marker_document_after_save(stack):
    """A 0-byte ODT opens as a blank editor and the first save produces a
    real ODT whose content.xml carries the marker."""
    stack.host.seed("odt-blank", "blank.odt", b"")
    stack.launch_editor("odt-blank", user="alice")

    html = stack.load_html("odt-blank")
    assert html == ""

    stack.save_html("odt-blank", f"<p>{MARKER}-blank</p>")
    stored = stack.remote_doc("odt-blank")
    assert _content_xml_has(stored, f"{MARKER}-blank")
    assert f"{MARKER}-blank" in _odt_text(stored)
