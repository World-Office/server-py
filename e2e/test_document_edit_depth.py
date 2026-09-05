"""Deep document-editing semantics against the LIVE WOPI surface.

The (file_id, access_token) pair is captured by spying on the editor's own
CheckFileInfo request after opening a document through the GUI — exactly the
session a real editor client holds. Verified protocol facts (2026-08-31):

* auth is via ``?access_token=`` query string (Bearer is rejected 400)
* file ids are 64-hex collaboration-service ids — NOT the OCIS GUID fileId
* PutFile REQUIRES ``X-WOPI-Override: PUT`` (without it the proxy 502s)
* LOCK/UNLOCK are NOT client-facing (405): the docserver serializes saves
  internally with its own upstream lock dance (see wo-docserver lock_tests)
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
    editor_frame,
    in_run_folder,  # noqa: F401  (fixture)
    open_file_by_name,
    run_id,  # noqa: F401  (fixture)
    wopi_get,
    wopi_info,
    wopi_open_and_capture,
    wopi_put,
)

import urllib3

urllib3.disable_warnings()


@pytest.fixture
def seeded_docx(in_run_folder, run_id):
    name = f"paper-{random.randint(1000, 9999)}.docx"
    r = dav_put(f"{run_id}/{name}", docx_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.locator(f"[data-test-resource-name='{name}']").wait_for(
        state="visible", timeout=25000
    )
    return in_run_folder, name, f"{run_id}/{name}"


def _capture(page, name):
    fid, tok = wopi_open_and_capture(page, name)
    page.wait_for_timeout(2000)
    return fid, tok


@pytest.mark.gui
@pytest.mark.wopi
def test_check_file_info_fields_after_open(seeded_docx):
    """CheckFileInfo must expose correct name/size/permissions to editors."""
    page, name, dav_path = seeded_docx
    file_id, token = _capture(page, name)
    r = wopi_info(file_id, token)
    assert r.status_code == 200, f"CheckFileInfo failed: {r.status_code}"
    info = r.json()
    assert info.get("BaseFileName") == name, f"BaseFileName mismatch: {info.get('BaseFileName')}"
    assert int(info.get("Size", -1)) > 0, "Size must be positive"
    assert info.get("UserCanWrite") is True, "editor user must be able to write"
    assert info.get("Version"), "Version must be non-empty"
    # capabilities a real word editor relies on
    assert info.get("SupportsUpdate") is True, f"SupportsUpdate missing: {info}"
    assert info.get("SupportsLocks") is True, f"SupportsLocks missing: {info}"
    assert info.get("HostEditUrl"), "HostEditUrl missing"


@pytest.mark.gui
@pytest.mark.wopi
def test_get_file_serves_the_stored_document(seeded_docx):
    """GetFile must serve valid docx bytes for the current revision."""
    page, name, dav_path = seeded_docx
    file_id, token = _capture(page, name)
    r = wopi_get(file_id, token)
    assert r.status_code == 200, f"GetFile failed: {r.status_code}"
    assert r.content[:2] == b"PK", f"GetFile is not a docx zip: {r.content[:20]!r}"
    doc = zipfile.ZipFile(BytesIO(r.content)).read("word/document.xml").decode("utf-8", "ignore")
    assert "E2E Anchor Title" in doc, f"seed content not served: {doc[:200]!r}"


@pytest.mark.gui
@pytest.mark.wopi
def test_put_file_roundtrip_and_last_write_wins(seeded_docx):
    """Two sequential raw PutFiles: content lands, last write wins."""
    page, name, dav_path = seeded_docx
    file_id, token = _capture(page, name)

    v1 = conftest._docx_bytes(["MARKER-V1-alpha", "MARKER-V1-beta"])
    v2 = conftest._docx_bytes(["MARKER-V2-gamma", "MARKER-V2-delta"])

    assert wopi_put(file_id, token, v1) == 200, "PutFile v1 rejected"
    assert dav_contains(dav_path, "MARKER-V1-alpha"), "v1 never reached storage"
    gf = wopi_get(file_id, token)
    assert b"MARKER-V1-alpha" in gf.content, "GetFile does not reflect v1"

    assert wopi_put(file_id, token, v2) == 200, "PutFile v2 rejected"
    assert dav_contains(dav_path, "MARKER-V2-gamma"), "v2 never reached storage"
    gf2 = wopi_get(file_id, token)
    assert b"MARKER-V2-gamma" in gf2.content, "GetFile does not reflect v2"
    assert b"MARKER-V1-alpha" not in gf2.content, "stale content served after overwrite"

    info = wopi_info(file_id, token).json()
    assert int(info.get("Size", -1)) > 0


@pytest.mark.gui
@pytest.mark.wopi
def test_put_file_requires_override_header(seeded_docx):
    """Without X-WOPI-Override: PUT the write must not be accepted."""
    page, name, dav_path = seeded_docx
    file_id, token = _capture(page, name)
    r = requests.post(
        f"{EDITOR_BASE}/wopi/files/{file_id}/contents",
        params={"access_token": token},
        data=b"rogue write",
        headers={"Content-Type": "application/octet-stream"},
        verify=False,
        timeout=30,
    )
    assert r.status_code != 200, f"override-less PutFile accepted: {r.status_code}"
    gr = dav_get(dav_path)
    assert gr.ok and b"rogue write" not in gr.content, "rejected write leaked to storage"


@pytest.mark.gui
@pytest.mark.wopi
def test_lock_endpoints_are_server_internal_not_client_facing(seeded_docx):
    """The docserver serializes saves internally; editors get 405 on LOCK.

    This pins the design: the upstream lock dance lives in wo-docserver's
    put_file (covered by its Rust unit tests), not in the client protocol.
    """
    page, name, dav_path = seeded_docx
    file_id, token = _capture(page, name)
    r = requests.post(
        f"{EDITOR_BASE}/wopi/files/{file_id}",
        params={"access_token": token},
        headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "client-lock"},
        verify=False,
        timeout=30,
    )
    assert r.status_code in (404, 405), f"client LOCK unexpectedly handled: {r.status_code}"


@pytest.mark.gui
@pytest.mark.wopi
def test_concurrent_puts_stay_valid(seeded_docx):
    """Simultaneous saves from two clients: no interleaved/corrupt result."""
    page, name, dav_path = seeded_docx
    file_id, token = _capture(page, name)
    a = conftest._docx_bytes(["CONCURRENT-A-from-ada"])
    b = conftest._docx_bytes(["CONCURRENT-B-from-bob"])

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(wopi_put, file_id, token, a), ex.submit(wopi_put, file_id, token, b)]
        statuses = [f.result() for f in futs]
    # the server-side lock dance serializes saves: the losing writer gets a
    # retryable rejection (409/502 in the current implementation) — what must
    # NEVER happen is silent corruption or interleaved content
    assert 200 in statuses, f"no winner among concurrent puts: {statuses}"
    assert all(s in (200, 409, 502) for s in statuses), (
        f"unexpected statuses from concurrent puts: {statuses}"
    )

    gf = wopi_get(file_id, token)
    assert gf.content[:2] == b"PK", "concurrent saves corrupted the zip"
    doc = zipfile.ZipFile(BytesIO(gf.content)).read("word/document.xml").decode("utf-8", "ignore")
    won_a, won_b = "CONCURRENT-A-from-ada" in doc, "CONCURRENT-B-from-bob" in doc
    assert won_a ^ won_b, f"neither or both writes won: a={won_a} b={won_b}"


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
    fr, canvas = editor_canvas(in_run_folder)
    canvas.click(position={"x": 100, "y": 130})
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
