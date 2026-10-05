"""Shared fixtures for the cloud-GUI E2E suite (OpenCloud web + embedded editors).

Targets the LIVE stack: https://cloud.graphwiz.ai (OCIS web) with the
World-Office docserver embedded at editor.cloud.graphwiz.ai.

Override via env:
    E2E_BASE         https://cloud.graphwiz.ai
    E2E_EDITOR_BASE  https://editor.cloud.graphwiz.ai
    E2E_USER / E2E_PASS
    E2E_HEADLESS     (default "1")

Everything the tests create lives inside a per-run folder `E2E-<runid>` in the
admin's personal space, removed again in the session finalizer.
"""

from __future__ import annotations

import os
import random
import time
import zipfile
from datetime import datetime, timezone

import sys

import pytest
import requests
from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE", "https://cloud.graphwiz.ai").rstrip("/")
EDITOR_BASE = os.environ.get("E2E_EDITOR_BASE", "https://editor.cloud.graphwiz.ai").rstrip("/")
USER = os.environ.get("E2E_USER", "admin")
PASS = os.environ.get("E2E_PASS", "wo-od-2026")
HEADLESS = os.environ.get("E2E_HEADLESS", "1") == "1"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# The VPN path to the stack is flaky; retry page loads.
GOTO_RETRIES = 3
LOGIN_SETTLE_MS = 5000
EDITOR_SETTLE_MS = 9000


# ---------------------------------------------------------------------------
# minimal deterministic document fixtures (no external templates needed)
# ---------------------------------------------------------------------------

def _docx_bytes(paragraphs: list[str]) -> bytes:
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def docx_bytes() -> bytes:
    """A docx with a title line and a body line — stable anchors for editing tests."""
    return _docx_bytes(["E2E Anchor Title", "Second anchor paragraph for editing."])


def pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def txt_bytes() -> bytes:
    return b"hello from the World-Office E2E suite\n"


# ---------------------------------------------------------------------------
# WebDAV helpers (admin's personal space)
# ---------------------------------------------------------------------------

def _dav() -> requests.Session:
    s = requests.Session()
    s.auth = (USER, PASS)
    s.verify = False
    _mount_retries(s)
    return s


def _mount_retries(session: requests.Session) -> None:
    """Retry connect/read failures and 5xx at the transport level.

    The public edge (caddy in front of the live stack) occasionally drops
    TCP connects for a second or two; without this, one blip fails a whole
    test (page loads succeed, the API probe right after times out).
    """
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=3,
        connect=3,
        read=2,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(
            {"GET", "PUT", "POST", "DELETE", "MKCOL", "MOVE", "PROPFIND", "PATCH", "LOCK", "UNLOCK"}
        ),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)


def dav_url(path: str) -> str:
    return f"{BASE}/remote.php/dav/files/{USER}/{path.lstrip('/')}"


def _retry_5xx(fn, *args, attempts: int = 4, **kwargs) -> requests.Response:
    """The posixfs id-cache intermittently degrades under heavy node churn
    ('record not found in cache' → 5xx). Retry with backoff; the cache
    usually recovers within seconds."""
    r = fn(*args, **kwargs)
    for attempt in range(attempts - 1):
        if r.status_code < 500:
            return r
        time.sleep(1.5 * (attempt + 1))
        r = fn(*args, **kwargs)
    return r


def dav_put(path: str, data: bytes) -> requests.Response:
    r = _retry_5xx(lambda: _dav().put(dav_url(path), data=data, timeout=30))
    if r.status_code == 409:
        # posixfs id-cache hiccup: after rapid E2E-* folder create/delete
        # cycles (session N teardown vs session N+1 setup) and/or the GUI
        # editor's WOPI/TUS save pipeline, reva can answer
        # `precondition failed: not found <folder>` (409) for a folder that
        # verifiably exists (MKCOL just returned 201). The cache heals
        # within seconds — nudge it with a parent PROPFIND (readdir/stat
        # repopulates the cache) and retry with backoff. OCIS PUT 409 is
        # never a legitimate "already exists" (an existing target is
        # overwritten with 204), so retries cannot mask a real conflict.
        parent = path.rsplit("/", 1)[0]
        for _wait in (0, 5, 15, 40):
            if _wait:
                time.sleep(_wait)
            _dav().request("PROPFIND", dav_url(parent), headers={"Depth": "1"}, timeout=30)
            r = _retry_5xx(lambda: _dav().put(dav_url(path), data=data, timeout=30))
            if r.status_code != 409:
                break
    return r


def dav_get(path: str) -> requests.Response:
    return _retry_5xx(lambda: _dav().get(dav_url(path), timeout=30))


def dav_delete(path: str) -> requests.Response:
    return _retry_5xx(lambda: _dav().delete(dav_url(path), timeout=30))


def dav_mkcol(path: str) -> requests.Response:
    return _retry_5xx(lambda: _dav().request("MKCOL", dav_url(path), timeout=30))


def dav_contains(path: str, token: str, timeout_s: float = 30.0) -> bool:
    """Poll WebDAV until `token` appears in the file (postprocessing lag).

    ZIP-based documents (docx/odt) store XML deflated — a raw-byte substring
    check can never see text inside them. Archive members are therefore
    decompressed before searching; plain files (txt/md) are checked raw.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = dav_get(path)
        if r.ok:
            if r.content[:2] == b"PK":
                import io
                import zipfile
                with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                    haystack = "".join(
                        zf.read(n).decode("utf-8", "ignore")
                        for n in zf.namelist()
                        if n.endswith((".xml", ".rdf"))
                    )
            else:
                haystack = r.content.decode("utf-8", "ignore")
            if token in haystack:
                return True
        time.sleep(2.0)
    return False


def dav_move(src: str, dest: str, overwrite: bool | None = None) -> requests.Response:
    """MOVE within the admin's DAV tree (`dest` is the full target path).

    overwrite=None sends no header (RFC 4918 default = Overwrite: T);
    overwrite=False sends `Overwrite: F` so a clash answers 412.
    """
    hdr = {"Destination": dav_url(dest)}
    if overwrite is False:
        hdr["Overwrite"] = "F"
    elif overwrite is True:
        hdr["Overwrite"] = "T"
    return _retry_5xx(lambda: _dav().request("MOVE", dav_url(src), headers=hdr, timeout=30))


def dav_propfind(path: str, depth: str = "1") -> requests.Response:
    return _retry_5xx(
        lambda: _dav().request("PROPFIND", dav_url(path), headers={"Depth": depth}, timeout=30)
    )


def ocs_shares(session: requests.Session | None = None, **params) -> list[dict]:
    """List OCS shares (optionally filtered), parsed from the JSON API."""
    s = session or _dav()
    params.setdefault("format", "json")
    r = s.get(
        f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares",
        params=params,
        headers={"OCS-APIRequest": "true"},
        timeout=20,
    )
    try:
        return r.json().get("ocs", {}).get("data", []) if r.ok else []
    except ValueError:
        return []


def ocs_create_share(path: str, share_type: int, **fields) -> tuple[int, dict | None]:
    """Create an OCS share; returns (http_status, share_dict_or_None).

    NB: the OCS POST endpoint ALWAYS answers XML (it ignores
    `format=json`), so the relevant fields are parsed from the XML tree.
    """
    import xml.etree.ElementTree as ET

    s = _dav()
    data = {"path": path, "shareType": str(share_type), **fields}
    r = s.post(
        f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares",
        data=data,
        headers={"OCS-APIRequest": "true"},
        timeout=20,
    )
    if not r.ok:
        return r.status_code, None
    try:
        data_el = ET.fromstring(r.text).find("data")
        if data_el is None:
            return r.status_code, None
        out = {}
        for child in data_el:
            out[child.tag] = (child.text or "").strip()
        return r.status_code, out
    except ET.ParseError:
        return r.status_code, None


def ocs_delete_share(share_id) -> int:
    r = _dav().delete(
        f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}",
        headers={"OCS-APIRequest": "true"},
        timeout=20,
    )
    return r.status_code


# ---------------------------------------------------------------------------
# GUI helpers
# ---------------------------------------------------------------------------

def goto(page, url: str):
    last_err = None
    for _ in range(GOTO_RETRIES):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            return
        except Exception as e:  # VPN blips
            last_err = e
    raise last_err


def wait_row(page, name: str, timeout_s: int = 90):
    """Wait until a specific resource row is visible, reloading periodically.

    The files listing is served through reva's readdir cache, which
    intermittently serves stale listings under churn — a freshly created
    (or long-existing!) file can be missing from the list for a while even
    though other rows render. Reload until it shows up.
    """
    row = page.locator(f"[data-test-resource-name='{name}']").first
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if row.count() and row.is_visible():
            return row
        page.wait_for_timeout(2500)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    raise AssertionError(f"row '{name}' never appeared in the listing after {timeout_s}s")


def login(page, user: str = USER, password: str = PASS):
    goto(page, BASE)
    page.locator("#oc-login-username").fill(user)
    page.locator("#oc-login-password").fill(password)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(LOGIN_SETTLE_MS)


def find_btn(page, label: str):
    """Resilient toolbar-button finder: aria-label, data-test-id, or exact text.

    OpenCloud web is inconsistent about which attribute carries UI labels.
    """
    for sel in (f"[aria-label={label!r}]", f"[data-test-id={label!r}]"):
        loc = page.locator(f"{sel} >> visible=true")
        if loc.count():
            return loc.first
    loc = page.get_by_role("button", name=label, exact=True)
    if loc.count():
        return loc.first
    return None


def open_file_by_name(page, name: str):
    """Open a file via the GUI.

    docx opens the World-Office editor directly; other types may show an
    "open with" bar (.open-file-bar) or need the context-menu Open action.
    """
    row = page.locator(f"[data-test-resource-name={name!r}]").first
    if row.count() == 0:
        row = page.locator(f"[data-test-resource-name]:has-text({name!r})").first
    row.click()
    page.wait_for_timeout(2500)

    def _has_iframe() -> bool:
        return page.query_selector("iframe") is not None

    if not _has_iframe():
        bar_btn = page.locator(".open-file-bar a, .open-file-bar button").first
        if bar_btn.count() and bar_btn.is_visible():
            try:
                bar_btn.click(timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(EDITOR_SETTLE_MS)
    if not _has_iframe():
        try:
            row.click(button="right")
            page.wait_for_timeout(1200)
            page.locator("[role=menuitem]:has-text('Open')").first.click(timeout=5000)
            page.wait_for_timeout(EDITOR_SETTLE_MS)
        except Exception:
            pass
    page.wait_for_timeout(1500)


def editor_frame(page):
    """The World-Office editor iframe inside OCIS web."""
    el = page.query_selector("iframe")
    assert el is not None, "no editor iframe found after opening a document"
    return el.content_frame()


def editor_canvas(page):
    """Return (frame, editable surface) of the live editor.

    Kept for call-site compatibility: the editor surface is the
    contenteditable ``#editor`` div (the canvas-based writer is gone).
    """
    fr = editor_frame(page)
    surface = fr.locator("#editor").first
    surface.wait_for(state="visible", timeout=30000)
    return fr, surface


def close_editor(page, run_id=None, url=None, file_path=None):
    """Leave the editor cleanly BEFORE the page is torn down.

    Closing a page with a live editor iframe makes the collaboration host
    fire a late async WOPI/TUS save during teardown; that race poisons
    reva's id-cache — new-file PUTs into the folder answer 409 for ~5 min
    and root listings go stale. Navigating away first unloads the editor
    gracefully. Pass `url` for non-admin pages (e.g. bob's shares view).

    With `file_path` (DAV path of the edited file), also VERIFY that the
    WOPI lock actually released: an unlock beacon lost to a network blip
    leaks a ~30-minute lock that hides 'Rename'/'Move to' in the files app
    and 423/409s every later write to that file. Fail loudly instead of
    poisoning downstream tests.
    """
    if url is None:
        url = f"{BASE}/files/spaces/personal/admin/{run_id}"
    goto(page, url)
    page.wait_for_timeout(2000)
    if file_path:
        deadline = time.time() + 45
        while time.time() < deadline:
            if "lockdiscovery" not in dav_propfind(file_path).text:
                return
            time.sleep(2.5)
        raise AssertionError(
            f"WOPI lock on {file_path} did not release within 45s of the "
            "graceful editor unload — the unlock beacon was likely lost; "
            "later tests on this file would 409/423 (leaked ~30min lock)"
        )


def wopi_token_from_editor(page) -> tuple[str, str]:
    """Harvest (file_id, access_token) from any open editor iframe URL.

    NOTE: superseded by :func:`wopi_open_and_capture` — the docserver's WOPI
    ids are the 64-hex collaboration-service ids seen in the editor's own
    /wopi/files/<hex> requests, not the GUID fileId of the outer frame.
    """
    import re
    import urllib.parse

    for _ in range(20):
        for fr in page.frames:
            fid = re.search(r"/wopi/files/([^/?]+)", fr.url)
            tok = re.search(r"access_token=([^&\s]+)", fr.url)
            if fid and tok:
                return fid.group(1), urllib.parse.unquote(tok.group(1))
        page.wait_for_timeout(1000)
    raise AssertionError("no editor iframe with access_token found")


def wopi_open_and_capture(page, name: str) -> tuple[str, str]:
    """Open `name` in the WorldOffice editor and capture the WOPI session.

    Two stack shapes are supported:
    - modern (OpenCloud collaboration): the web POSTs the editor launch to
      ``/editor?WOPISrc=...`` with the access_token in the urlencoded body;
      the WOPISrc carries the 64-hex collaboration file id. The browser
      itself never calls /wopi/files (the docserver relays server-side).
    - legacy/local: the browser calls CheckFileInfo directly at
      ``/wopi/files/<id>?access_token=...``.

    Returns (file_id, access_token).
    """
    import re
    import urllib.parse

    holder = {"r": None}

    def _finish(fid, tok):
        if fid and tok and not holder["r"]:
            holder["r"] = (fid, tok)

    def _on_request(req):
        if holder["r"]:
            return
        url = req.url
        if "/editor" in url and "WOPISrc=" in url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            src = (q.get("WOPISrc") or [""])[0]
            m = re.search(r"/wopi/files/([0-9a-fA-F]{64})", src)
            tok = None
            if req.method == "POST" and req.post_data:
                body = urllib.parse.parse_qs(req.post_data)
                tok = (body.get("access_token") or [None])[0]
            if tok is None:
                tok = (q.get("access_token") or [None])[0]
            _finish(m.group(1) if m else None, tok)
            return
        m = re.search(r"/wopi/files/([0-9a-fA-F]{64})", url)
        if m:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            _finish(m.group(1), (q.get("access_token") or [None])[0])

    # The live OpenCloud web opens the editor as an iframe/popup, so listen
    # at context level — that sees the opener page, any popup, and every
    # frame at once.
    context = page.context
    context.on("request", _on_request)
    try:
        open_file_by_name(page, name)
        deadline = time.time() + 30
        while time.time() < deadline and not holder["r"]:
            page.wait_for_timeout(800)
        if not holder["r"]:
            seen = [pg.url[:100] for pg in context.pages]
            raise AssertionError(
                f"no editor launch or WOPI request observed (open pages: {seen})"
            )
        return holder["r"]
    finally:
        context.remove_listener("request", _on_request)


WOPI_PUT_HEADERS = {"X-WOPI-Override": "PUT", "Content-Type": "application/octet-stream"}


def wopi_put(file_id: str, token: str, payload: bytes) -> int:
    """Raw WOPI PutFile against the live docserver; returns the status code."""
    r = requests.post(
        f"{EDITOR_BASE}/wopi/files/{file_id}/contents",
        params={"access_token": token},
        data=payload,
        headers=WOPI_PUT_HEADERS,
        verify=False,
        timeout=30,
    )
    return r.status_code


def wopi_get(file_id: str, token: str):
    """Raw WOPI GetFile; returns the response."""
    return requests.get(
        f"{EDITOR_BASE}/wopi/files/{file_id}/contents",
        params={"access_token": token},
        verify=False,
        timeout=30,
    )


def wopi_info(file_id: str, token: str):
    """Raw WOPI CheckFileInfo; returns the response."""
    return requests.get(
        f"{EDITOR_BASE}/wopi/files/{file_id}",
        params={"access_token": token},
        verify=False,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"E2E-{stamp}-{random.randint(100, 999)}"


@pytest.fixture(scope="session", autouse=True)
def _dav_health_probe():
    """Warn loudly when the opencloud posixfs id-cache is degraded.

    Symptom: fresh MKCOL+PUT answer 5xx with 'record not found in cache'
    in the opencloud logs. The suite cannot pass while degraded — restart
    the opencloud container first:
        cd ~/opencloud-compose && docker compose restart opencloud
    """
    import time as _t

    folder = f"E2E-health-{random.randint(100, 999)}"
    ok = False
    for attempt in range(3):
        if dav_mkcol(folder).status_code == 201:
            if dav_put(f"{folder}/probe.txt", b"ok").status_code == 201:
                ok = True
        dav_delete(folder)
        if ok:
            break
        _t.sleep(3)
    if not ok:
        print(
            "\n!!! opencloud DAV degraded (MKCOL/PUT 5xx after retries). "
            "Restart opencloud before running the suite: "
            "docker compose restart opencloud\n",
            file=sys.stderr,
        )
    yield


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def session_ctx(pw, run_id):
    """Session-wide context that is logged in once (speeds up GUI tests)."""
    ctx = pw.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 900},
        user_agent=UA,
    )
    page = ctx.new_page()
    login(page)
    assert "/files/" in page.url or "login" not in page.url.lower(), (
        f"login appears to have failed, url={page.url}"
    )
    # create the run folder and move into it for the session
    resp = dav_mkcol(run_id)
    assert resp.status_code in (201, 405), f"MKCOL failed: {resp.status_code}"
    # absorb the posixfs id-cache healing window HERE, once: back-to-back
    # pytest sessions (session N-1 deleted its E2E-* folder seconds ago) can
    # leave reva's id-cache incoherent — MKCOL succeeds but the first PUT
    # answers `precondition failed: not found <folder>` (409) for a while.
    # Probe-write until it sticks (≤ ~60 s) so real tests never see it.
    # NB: no leading dot — OCIS rejects dot-files with exactly the 409 we
    # are probing for, which would poison the probe itself
    _probe = dav_put(f"{run_id}/cache-probe.txt", b"ok")
    _t0 = time.time()
    # the wedge from a previous session's editor teardown lasts up to ~285 s
    while _probe.status_code == 409 and time.time() - _t0 < 320:
        time.sleep(10)
        _probe = dav_put(f"{run_id}/cache-probe.txt", b"ok")
    assert _probe.status_code in (201, 204), (
        f"run folder unusable after MKCOL (id-cache degraded?): {_probe.status_code}. "
        "Runbook: cd ~/opencloud-compose && docker compose restart opencloud"
    )
    dav_delete(f"{run_id}/cache-probe.txt")
    page.close()
    yield ctx
    # cleanup: remove the whole run folder
    try:
        dav_delete(run_id)
    except Exception:
        pass
    ctx.close()


@pytest.fixture
def page(session_ctx):
    pg = session_ctx.new_page()
    yield pg
    _graceful_editor_unload(pg)
    pg.close()


def ensure_user(username: str, password: str, display_name: str) -> str:
    """Idempotently provision a local user on the live stack; return their id.

    Looks the user up by `onPremisesSamAccountName` (falling back to their
    mail), creates them via the Graph API when missing, and (re)sets their
    password either way. This replaces an old hardcoded UUID that went stale
    when the IDM database was re-provisioned — user ids are server-generated
    and MUST NOT be assumed by tests.
    """
    import requests

    s = requests.Session()
    s.auth = (USER, PASS)
    s.verify = False
    _mount_retries(s)

    def _find():
        r = s.get(f"{BASE}/graph/v1.0/users", timeout=30)
        if r.ok:
            for u in r.json().get("value", []):
                if u.get("onPremisesSamAccountName") == username or (
                    u.get("mail") == f"{username}@example.org"
                ):
                    return u["id"]
        return None

    uid = _find()
    if uid is None:
        r = s.post(
            f"{BASE}/graph/v1.0/users",
            json={
                "displayName": display_name,
                "onPremisesSamAccountName": username,
                "mail": f"{username}@example.org",
                "accountEnabled": True,
                "passwordProfile": {"password": password},
            },
            timeout=30,
        )
        assert r.status_code in (200, 201), f"user create failed: {r.status_code} {r.text[:200]}"
        uid = r.json()["id"]
    r = s.patch(
        f"{BASE}/graph/v1.0/users/{uid}",
        json={"passwordProfile": {"password": password}},
        timeout=30,
    )
    assert r.status_code == 200, f"password reset failed: {r.status_code}"
    return uid


def _graceful_editor_unload(pg):
    """Leave an open editor gracefully BEFORE the page is closed.

    An abrupt close with a live editor makes the collaboration host fire a
    late async WOPI/TUS save that wedges reva's id-cache for minutes (dav_put
    then answers 409 for every new upload). Navigating away first lets the
    editor unregister cleanly — same rationale as close_editor().
    """
    try:
        if "external-worldoffice" in pg.url or pg.url.rstrip("/").endswith("/editor"):
            pg.goto(f"{BASE}/files/spaces/personal/admin",
                    wait_until="domcontentloaded", timeout=15000)
            pg.wait_for_timeout(2000)
    except Exception:
        pass


@pytest.fixture
def fresh_ctx(pw):
    """Un-logged-in context for auth tests."""
    ctx = pw.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 900},
        user_agent=UA,
    )
    yield ctx
    ctx.close()


@pytest.fixture
def fresh_page(fresh_ctx):
    pg = fresh_ctx.new_page()
    yield pg
    pg.close()


@pytest.fixture
def in_run_folder(page, run_id):
    """Navigate the logged-in page into the run folder.

    On teardown, unload any editor the test left open — an abrupt page close
    leaks the ~30-minute WOPI lock and fires a late async save that poisons
    reva's id-cache (fresh-node 409s for minutes) for every later module.
    """
    goto(page, f"{BASE}/files/spaces/personal/admin/{run_id}")
    page.wait_for_timeout(2500)
    yield page
    try:
        close_editor(page, run_id)
    except Exception:
        pass


@pytest.fixture
def uploaded_docx(in_run_folder, run_id):
    """A docx in the run folder, opened in the word editor; returns (page, name)."""
    name = f"e2e-edit-{random.randint(1000, 9999)}.docx"
    path = f"{run_id}/{name}"
    r = dav_put(path, docx_bytes())
    assert r.status_code in (201, 204), f"docx upload failed: {r.status_code}"
    in_run_folder.wait_for_timeout(1500)
    yield in_run_folder, name, path
    # loud lock check: this file WAS opened in the editor by the test
    close_editor(in_run_folder, run_id, file_path=path)
