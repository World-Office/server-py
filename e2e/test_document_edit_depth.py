"""Deep document-editing semantics against the LIVE docserver.

Contract as deployed 2026-09-06 (commit 6f2c2c6af era):

The docserver exposes TWO document worlds, and the old "proxy" facts
(2026-08-31) are gone:

* LOCAL documents (created via ``POST /api/documents/new``) live in the
  docserver's own store and are served by the WOPI protocol surface
  ``/wopi/files/<doc_id>``. That surface is unauthenticated by design
  (it sits behind the edge, tokens are not validated locally).
* OPENCLOUD documents (launched from the web UI) are session-based: the
  launch registers an EditorSession keyed by the collaboration-service
  file id; the browser edits through ``/api/documents/<id>/*`` and saves
  forward to the remote WOPI host (OpenCloud DAV). They are NOT in the
  local store, so raw ``/wopi/files/<collab-id>`` calls 404 — by design.

Verified protocol facts (2026-09-06, live stack):

* CheckFileInfo payload has no HostEditUrl (that came from the old
  upstream proxy); Version/SupportsUpdate/SupportsLocks/UserCanWrite exist
* the LOCAL PutFile accepts writes without ``X-WOPI-Override: PUT``
  (the requirement lived in the old proxy chain); it DOES honour locks
* ``POST /wopi/files/<id>`` (bare, no /lock suffix) has no route → 405:
  client-facing LOCK is not part of the surface (design: saves serialize
  via the internal lock dance)
* concurrent PutFiles: a foreign lock yields 409 (X-WOPI-Lock echoes the
  winner), never corruption
* a dirty GUI save roundtrips to OpenCloud storage as a valid OOXML zip
"""

import random
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
import requests

import conftest
from conftest import (
    EDITOR_BASE,
    close_editor,
    dav_contains,
    dav_get,
    dav_put,
    docx_bytes,
    editor_canvas,
    in_run_folder,  # noqa: F401  (fixture)
    open_file_by_name,
    run_id,  # noqa: F401  (fixture)
    wopi_get,
    wopi_info,
    wopi_put,
)

import urllib3

urllib3.disable_warnings()


# ---------------------------------------------------------------------------
# local-document fixtures (no GUI: the local WOPI surface is API-first)
# ---------------------------------------------------------------------------

@pytest.fixture
def local_docx():
    """A document in the docserver's LOCAL store, created via the API."""
    r = requests.post(
        f"{EDITOR_BASE}/api/documents/new",
        params={"format": "docx"},
        verify=False,
        timeout=30,
    )
    assert r.status_code == 200, f"document creation failed: {r.status_code}"
    doc_id = r.json()["doc_id"]
    assert doc_id, f"no doc_id in response: {r.json()}"
    return doc_id


def _docx_text(content: bytes) -> str:
    return zipfile.ZipFile(BytesIO(content)).read("word/document.xml").decode("utf-8", "ignore")


# ---------------------------------------------------------------------------
# local WOPI protocol surface
# ---------------------------------------------------------------------------

@pytest.mark.wopi
def test_check_file_info_fields(local_docx):
    """CheckFileInfo must expose the fields a real editor client needs."""
    r = wopi_info(local_docx, "")
    assert r.status_code == 200, f"CheckFileInfo failed: {r.status_code}"
    info = r.json()
    assert info.get("BaseFileName") == "untitled.docx", (
        f"BaseFileName mismatch: {info.get('BaseFileName')}"
    )
    assert int(info.get("Size", -1)) > 0, "Size must be positive"
    assert info.get("UserCanWrite") is True, "local documents are writable"
    assert info.get("Version"), "Version must be non-empty"
    # capabilities a real word editor relies on
    assert info.get("SupportsUpdate") is True, f"SupportsUpdate missing: {info}"
    assert info.get("SupportsLocks") is True, f"SupportsLocks missing: {info}"


@pytest.mark.wopi
def test_get_file_serves_stored_bytes(local_docx):
    """GetFile must serve valid docx bytes."""
    r = wopi_get(local_docx, "")
    assert r.status_code == 200, f"GetFile failed: {r.status_code}"
    assert r.content[:2] == b"PK", f"GetFile is not a docx zip: {r.content[:20]!r}"
    assert "[Content_Types].xml" in zipfile.ZipFile(BytesIO(r.content)).namelist(), (
        "zip incomplete"
    )


@pytest.mark.wopi
def test_put_file_roundtrip_and_last_write_wins(local_docx):
    """Two sequential raw PutFiles: content lands, last write wins."""
    v1 = conftest._docx_bytes(["MARKER-V1-alpha", "MARKER-V1-beta"])
    v2 = conftest._docx_bytes(["MARKER-V2-gamma", "MARKER-V2-delta"])

    assert wopi_put(local_docx, "", v1) == 200, "PutFile v1 rejected"
    gf = wopi_get(local_docx, "")
    assert b"MARKER-V1-alpha" in gf.content, "GetFile does not reflect v1"

    assert wopi_put(local_docx, "", v2) == 200, "PutFile v2 rejected"
    gf2 = wopi_get(local_docx, "")
    assert b"MARKER-V2-gamma" in gf2.content, "GetFile does not reflect v2"
    assert b"MARKER-V1-alpha" not in gf2.content, "stale content served after overwrite"

    info = wopi_info(local_docx, "").json()
    assert int(info.get("Size", -1)) == len(v2), "CheckFileInfo Size not updated"


@pytest.mark.wopi
def test_put_file_honours_locks(local_docx):
    """PutFile must not clobber a foreign lock; the winner's token is echoed.

    (Replaces the old override-header test: the X-WOPI-Override requirement
    lived in the retired proxy chain — the local surface accepts header-less
    writes but enforces locks strictly.)
    """
    LOCK_HEADER = "X-WOPI-Lock"  # mirrors src/wopi/protocol.py

    # acquire a lock as writer A
    r = requests.post(
        f"{EDITOR_BASE}/wopi/files/{local_docx}/lock",
        headers={"X-WOPI-Lock": "writer-ada"},
        verify=False,
        timeout=30,
    )
    assert r.status_code == 200, f"Lock acquisition failed: {r.status_code}"

    # writer B's PutFile must be rejected with the winner's token echoed
    put = requests.post(
        f"{EDITOR_BASE}/wopi/files/{local_docx}/contents",
        data=conftest._docx_bytes(["rogue write"]),
        headers={"Content-Type": "application/octet-stream", LOCK_HEADER: "writer-bob"},
        verify=False,
        timeout=30,
    )
    assert put.status_code == 409, f"foreign-lock PutFile not rejected: {put.status_code}"
    assert put.headers.get("X-WOPI-Lock") == "writer-ada", (
        f"lock token not echoed: {put.headers.get('X-WOPI-Lock')!r}"
    )
    assert b"rogue write" not in wopi_get(local_docx, "").content, "rejected write leaked"

    # the rightful writer can still save; an empty lock token is rejected
    ok = requests.post(
        f"{EDITOR_BASE}/wopi/files/{local_docx}/contents",
        data=conftest._docx_bytes(["ada write"]),
        headers={"Content-Type": "application/octet-stream", LOCK_HEADER: "writer-ada"},
        verify=False,
        timeout=30,
    )
    assert ok.status_code == 200, f"lock-owner PutFile rejected: {ok.status_code}"
    assert b"ada write" in wopi_get(local_docx, "").content


@pytest.mark.wopi
def test_lock_endpoints_are_server_internal_not_client_facing(local_docx):
    """``POST /wopi/files/<id>`` (bare WOPI LOCK, no /lock suffix) is 405.

    This pins the design: the upstream lock dance lives server-side, not in
    the client protocol surface.
    """
    r = requests.post(
        f"{EDITOR_BASE}/wopi/files/{local_docx}",
        headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "client-lock"},
        verify=False,
        timeout=30,
    )
    assert r.status_code in (404, 405), f"client LOCK unexpectedly handled: {r.status_code}"


@pytest.mark.wopi
def test_concurrent_puts_stay_valid(local_docx):
    """Simultaneous saves from two clients: no interleaved/corrupt result."""
    a = conftest._docx_bytes(["CONCURRENT-A-from-ada"])
    b = conftest._docx_bytes(["CONCURRENT-B-from-bob"])

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(wopi_put, local_docx, "", a), ex.submit(wopi_put, local_docx, "", b)]
        statuses = [f.result() for f in futs]
    # the store serializes writes: what must NEVER happen is silent
    # corruption or interleaved content
    assert all(s == 200 for s in statuses), f"unexpected statuses: {statuses}"

    gf = wopi_get(local_docx, "")
    assert gf.content[:2] == b"PK", "concurrent saves corrupted the zip"
    doc = _docx_text(gf.content)
    won_a, won_b = "CONCURRENT-A-from-ada" in doc, "CONCURRENT-B-from-bob" in doc
    assert won_a ^ won_b, f"neither or both writes won: a={won_a} b={won_b}"


# ---------------------------------------------------------------------------
# GUI roundtrip: a dirty editor save must reach OpenCloud storage intact
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_docx(in_run_folder, run_id):
    name = f"paper-{random.randint(1000, 9999)}.docx"
    r = dav_put(f"{run_id}/{name}", docx_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.locator(f"[data-test-resource-name='{name}']").wait_for(
        state="visible", timeout=25000
    )
    return in_run_folder, name, f"{run_id}/{name}"


@pytest.mark.gui
@pytest.mark.wopi
def test_editor_save_preserves_document_integrity(in_run_folder, run_id):
    """A dirty editor save must keep a valid OOXML zip with the seed text."""
    name = f"integrity-{random.randint(1000, 9999)}.docx"
    dav_path = f"{run_id}/{name}"
    r = dav_put(dav_path, docx_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.locator(f"[data-test-resource-name='{name}']").wait_for(state="visible", timeout=25000)

    open_file_by_name(in_run_folder, name)
    fr, surface = editor_canvas(in_run_folder)
    surface.click()
    in_run_folder.keyboard.type("X", delay=25)  # mark dirty so a save fires
    in_run_folder.keyboard.press("Control+s")
    in_run_folder.wait_for_timeout(6000)

    gr = dav_get(dav_path)
    assert gr.ok, f"fetch failed: {gr.status_code}"
    assert gr.content[:2] == b"PK", "saved file must remain a valid OOXML zip"
    zf = zipfile.ZipFile(BytesIO(gr.content))
    assert "[Content_Types].xml" in zf.namelist(), f"zip incomplete: {zf.namelist()}"
    doc_xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    assert "E2E Anchor Title" in doc_xml, f"seed text lost on save: {doc_xml[:200]}"

    # leave the editor cleanly — an abrupt page close with a live editor
    # makes the collaboration host fire a late save that wedges the run
    # folder's id-cache entry for minutes (see conftest.close_editor)
    close_editor(in_run_folder, run_id)
