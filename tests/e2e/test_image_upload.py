"""E2E — Bild-Einsetzen via Upload (insert image with upload dialog).

This suite drives the exact code path the docserver uses in production
(deployed against real OpenCloud / OCIS, e.g. cloud.graphwiz.ai), focused
on the image-insert feature the editor UI offers (`#btn-image` ->
upload dialog -> self-contained data-URI <img> at the caret):

    seed an ODT on the OpenCloud host
      -> OpenCloud launches /editor with access_token + WOPISrc  (WOPI handshake)
      -> GET  /api/documents/{id}/html                           (ODT -> HTML)
      -> "user uploads a picture": the HTML gains a self-contained
         <img src="data:image/png;base64,..."> — exactly what the
         dialog's FileReader produces before the Insert button runs
         document.execCommand("insertHTML", ...)
      -> POST /api/documents/{id}/save                           (HTML -> ODT)
      -> docserver PUTs the ODT back to the remote host          (PutFile)
      -> GET  /api/documents/{id}/html again                     (reload)
      -> re-read the ODT stored on the remote host and verify its
         content.xml carries the picture (draw:frame/draw:image) and
         the package's Pictures/ member bytes match the uploaded file.

A real HTTP WOPI host stands in for OCIS (same wire protocol, same
lock-before-PutFile semantics the wopiserver enforces). The real
docserver app, editor router, RemoteWopiClient, HTML sanitizer and the
ODT converter (which embeds data-URI images as draw:frame/draw:image
binaries) are exercised end to end — nothing is mocked on the docserver
side.

The suite asserts the image-persistence contract: a picture inserted via
the upload dialog survives every hop of the production path, and the
stored bytes are a real ODT whose package holds the very same picture
bytes.

Marker emitted when the full production-path image persistence check
passes:
IMAGE-PERSISTENCE: OK
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import struct
import threading
import zipfile
import zlib
from wsgiref.simple_server import make_server

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from odf.draw import Frame, Image
from odf.opendocument import OpenDocumentText, load
from odf.text import P

from src.config import Config
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router

# Marker the "user" types next to an inserted picture; a save is never
# treated as successful until the marker is found in the stored ODT.
MARKER = "IMAGE-PERSISTENCE"


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

        # GetFile — the docserver reads the raw document bytes with the token.
        if method == "GET" and is_contents:
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/octet-stream"),
                    ("X-WOPI-ItemVersion", "v1"),
                ],
            )
            return [self.content[doc_id]]

        # CheckFileInfo — the docserver reads BaseFileName (routes .odt to the
        # right converter) and UserId (names the WOPI lock).
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

        @contextlib.asynccontextmanager
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
        assert self.host.put_count >= 1, "save must have PUT bytes to OpenCloud"
        assert self.host.put_lock_headers and self.host.put_lock_headers[-1], (
            "save must present the WOPI lock taken at launch"
        )

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
# Uploaded-picture fixtures + stored-bytes inspection
# ----------------------------------------------------------------------

def _png_bytes(width: int, height: int) -> bytes:
    """Build a minimal valid PNG (RGB) of the given pixel size — the kind of
    small file a user would pick in the insert-image dialog."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + ctype + data
        return c + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


def _data_uri(data: bytes, mime: str = "image/png") -> str:
    """The self-contained src the upload dialog's FileReader would produce."""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _decode_data_uri(uri: str) -> bytes:
    return base64.b64decode(uri.split(",", 1)[1])


def _img_srcs(html: str) -> list[str]:
    """All data-URI src values of <img> tags in an HTML fragment."""
    return re.findall(r'<img[^>]*\ssrc="(data:[^"]+)"', html)


def _odt_picture_bytes(odt: bytes) -> list[bytes]:
    """Bytes of every Pictures/ member in an ODT package."""
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        return [z.read(n) for n in z.namelist() if n.startswith("Pictures/")]


def _odt_content_xml(odt: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        return z.read("content.xml").decode("utf-8", "replace")


def _odt_text(odt: bytes) -> str:
    from odf import teletype

    doc = load(io.BytesIO(odt))
    return teletype.extractText(doc.text)


def _build_odt(*lines: str) -> bytes:
    """Build an ODT with one plain paragraph per line."""
    doc = OpenDocumentText()
    for line in lines:
        doc.text.addElement(P(text=line))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_odt_with_picture(png: bytes, caption: str = "imaged ") -> bytes:
    """Build an ODT whose paragraph embeds a draw:frame/draw:image."""
    doc = OpenDocumentText()
    name = doc.addPictureFromString(png, "image/png")
    frame = Frame(name="Pic", anchortype="as-char")
    frame.addElement(Image(href=name))
    p = P()
    p.addText(caption)
    p.addElement(frame)
    doc.text.addElement(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------
# The E2E tests — Bild-Einsetzen via Upload
# ----------------------------------------------------------------------

def test_odt_image_upload_persists_through_production_opencloud(stack):
    """The full production roundtrip for a picture inserted via the upload
    dialog: OpenCloud launch -> HTML -> craft the self-contained data-URI
    <img> the dialog inserts -> save -> PutFile -> reload. Emits the
    IMAGE-PERSISTENCE marker only when the ODT stored back on the OpenCloud
    host carries the very same picture bytes (content.xml draw:frame/
    draw:image + Pictures/ package member)."""
    stack.host.seed("img-upload", "report.odt", _build_odt("Draft report"))
    stack.launch_editor("img-upload", user="alice")

    # OpenCloud serves the ODT as HTML for the editor.
    html = stack.load_html("img-upload")
    assert "Draft report" in html
    assert _img_srcs(html) == [], "no images yet"

    # The user picks picture.png in the upload dialog; the dialog turns it
    # into a self-contained data URI and inserts <img> at the caret (the
    # same markup document.execCommand("insertHTML", ...) produces), then
    # types a caption next to it and saves.
    png = _png_bytes(3, 2)
    uploaded = f'<p>{MARKER}: Kapitel <img src="{_data_uri(png)}" alt="logo.png"/></p>'
    stack.save_html("img-upload", html + "\n" + uploaded)

    # The docserver must have PUT the ODT back to the OpenCloud host,
    # presenting the WOPI lock it took at launch (409 otherwise).
    assert stack.host.put_count == 1, "edited doc must be PUT back to OpenCloud"
    assert stack.host.put_overrides == ["PUT"]
    assert stack.host.put_lock_headers and stack.host.put_lock_headers[0], (
        "save must present the WOPI lock taken at launch"
    )

    # Reload from the remote host: the picture comes back as an <img> whose
    # data URI decodes to the exact uploaded bytes, next to the caption.
    reloaded = stack.load_html("img-upload")
    assert MARKER in reloaded
    srcs = _img_srcs(reloaded)
    assert len(srcs) == 1, reloaded
    assert _decode_data_uri(srcs[0]) == png, "reloaded picture bytes must match upload"
    assert 'alt="logo.png"' in reloaded

    # The stored bytes are a real ODT: content.xml carries the picture as
    # draw:frame/draw:image and the package embeds the original bytes.
    stored = stack.remote_doc("img-upload")
    content = _odt_content_xml(stored)
    assert "draw:frame" in content and "draw:image" in content, content
    pictures = _odt_picture_bytes(stored)
    assert len(pictures) == 1
    assert pictures[0] == png, "stored ODT picture bytes must match the upload"
    assert MARKER in _odt_text(stored)

    print("IMAGE-PERSISTENCE: OK")


def test_image_upload_into_blank_odt_first_save(stack):
    """Starting from an empty ODT, a picture uploaded as the document's only
    content lands on the remote host as a real ODT with the image embedded."""
    stack.host.seed("img-blank", "pic.odt", b"")
    stack.launch_editor("img-blank", user="alice")

    html = stack.load_html("img-blank")
    assert html == ""

    png = _png_bytes(4, 1)
    stack.save_html("img-blank", f'<p><img src="{_data_uri(png)}"/></p>')
    assert stack.host.put_count == 1

    stored = stack.remote_doc("img-blank")
    content = _odt_content_xml(stored)
    assert "draw:frame" in content and "draw:image" in content, content
    assert _odt_picture_bytes(stored) == [png]

    reloaded = stack.load_html("img-blank")
    srcs = _img_srcs(reloaded)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png


def test_image_upload_keeps_alt_and_surrounding_text(stack):
    """The uploaded picture's alt text becomes the ODF standard svg:title on
    the draw:frame and is re-exported as an alt attribute on reload, with
    the surrounding text order preserved."""
    stack.host.seed("img-alt", "alt.odt", _build_odt("Intro line"))
    stack.launch_editor("img-alt", user="alice")

    png = _png_bytes(2, 2)
    edited = (
        stack.load_html("img-alt")
        + f'\n<p>before <img src="{_data_uri(png)}" alt="Kitten Foto"/> after</p>'
        + f"\n<p>{MARKER}-tail</p>"
    )
    stack.save_html("img-alt", edited)

    stored = stack.remote_doc("img-alt")
    content = _odt_content_xml(stored)
    assert "Kitten Foto" in content, "alt must become svg:title in content.xml"
    assert _odt_picture_bytes(stored) == [png]

    reloaded = stack.load_html("img-alt")
    assert "before " in reloaded and " after" in reloaded
    assert f"{MARKER}-tail" in reloaded
    assert 'alt="Kitten Foto"' in reloaded
    srcs = _img_srcs(reloaded)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png


def test_picture_in_seeded_odt_survives_edit_and_save(stack):
    """A document that already contains a picture (draw:frame/draw:image)
    loads into the editor as a data-URI <img>; editing next to it and
    saving keeps the picture bytes verbatim on the remote host."""
    png = _png_bytes(5, 4)
    stack.host.seed("img-seeded", "seeded.odt", _build_odt_with_picture(png))
    stack.launch_editor("img-seeded", user="alice")

    html = stack.load_html("img-seeded")
    srcs = _img_srcs(html)
    assert len(srcs) == 1, html
    assert _decode_data_uri(srcs[0]) == png

    # The user adds a caption paragraph after the picture and saves.
    stack.save_html("img-seeded", html + f"\n<p>{MARKER}-caption</p>")
    assert stack.host.put_count == 1
    assert stack.host.put_lock_headers and stack.host.put_lock_headers[0]

    stored = stack.remote_doc("img-seeded")
    assert _odt_picture_bytes(stored) == [png], "picture bytes must survive the edit"
    content = _odt_content_xml(stored)
    assert "draw:frame" in content and "draw:image" in content
    assert f"{MARKER}-caption" in _odt_text(stored)

    reloaded = stack.load_html("img-seeded")
    srcs = _img_srcs(reloaded)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png
    assert f"{MARKER}-caption" in reloaded


def test_image_upload_survives_close_and_reopen(stack):
    """After a save (remote PutFile) and a fresh launch (unlock + re-open as
    another user), the uploaded picture is still served to the editor."""
    stack.host.seed("img-reopen", "reopen.odt", _build_odt("Open"))
    stack.launch_editor("img-reopen", user="alice")

    png = _png_bytes(3, 3)
    stack.save_html("img-reopen", f'<p>{MARKER}-reopen <img src="{_data_uri(png)}"/></p>')
    assert stack.host.put_count == 1

    # Close the document: unlock on the remote host, then reopen it through
    # a fresh launch as another user (the full close/reopen cycle).
    stack.unlock("img-reopen")
    assert not stack.host.locks.get("img-reopen"), "unlock must release the remote lock"
    stack.launch_editor("img-reopen", user="bob")

    again = stack.load_html("img-reopen")
    assert f"{MARKER}-reopen" in again
    srcs = _img_srcs(again)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png

    stored = stack.remote_doc("img-reopen")
    assert _odt_picture_bytes(stored) == [png]


def test_unsafe_image_urls_are_dropped_on_save(stack):
    """The upload path must not weaken sanitization: an <img> whose src is a
    script scheme (javascript:) is dropped before conversion, so neither an
    executable URL nor a stray picture reaches the stored bytes."""
    from src.editor.sanitize import sanitize_html

    stack.host.seed("img-safe", "safe.odt", _build_odt("Clean"))
    stack.launch_editor("img-safe", user="alice")

    # Pretend a hostile upload: the dialog would never produce this, but a
    # hand-crafted POST to the save endpoint must still be neutralized. The
    # sanitizer keeps the harmless <img> shell but drops the script-scheme
    # src, and the save path must never embed a picture from it.
    evil = '<p>x</p><img src="javascript:alert(1)">'
    cleaned = sanitize_html(evil)
    assert "javascript:" not in cleaned
    assert "src=" not in cleaned, cleaned

    stack.save_html("img-safe", evil)
    stored = stack.remote_doc("img-safe")
    assert _odt_picture_bytes(stored) == [], "no picture may be embedded from a script URL"
    assert "alert" not in _odt_content_xml(stored)


def test_image_resize_width_height_roundtrips_production_opencloud(stack):
    """Explicit width/height on a data-URI <img> (what the resize fields in
    the insert-image dialog emit) survive save -> ODT draw:frame dims ->
    reload, through the real production path."""
    png = _png_bytes(4, 2)
    stack.host.seed("img-resize", "resize.odt", b"")
    stack.launch_editor("img-resize", user="alice")

    stack.save_html(
        "img-resize",
        f'<p><img src="{_data_uri(png)}" alt="wide" width="240" height="120"/></p>',
    )
    assert stack.host.put_count == 1

    stored = stack.remote_doc("img-resize")
    content = _odt_content_xml(stored)
    # The draw:frame carries the requested dimensions (svg:width/height px).
    assert "svg:width=\"240px\"" in content, content
    assert "120px" in content, content

    reloaded = stack.load_html("img-resize")
    assert 'width="240"' in reloaded and 'height="120"' in reloaded, reloaded
    assert _decode_data_uri(_img_srcs(reloaded)[0]) == png

    print("IMAGE-RESIZE-ROUNDTRIP: OK")
