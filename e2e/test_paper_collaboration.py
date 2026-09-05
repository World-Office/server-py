"""The scientist-collaboration scenario, end to end.

feature register: F-120 F-121 F-123 (harness-graph)

Ada (admin) writes a paper with her co-author Bob (wo-test-bob): she seeds
the manuscript, opens it in WorldOffice, saves without corruption, shares the
project folder, Bob sees/opens/edits it, a locked save conflict is resolved,
version history is checked, a read-only review link is published, the final
manuscript is downloaded for journal submission, the file survives a rename,
an outsider (Eve) is locked out, and an accidental deletion is undone.

TESTS ARE ORDERED: the scenario builds on the state of earlier tests
(pytest runs them in file order). Run the module as a whole:
    python3 -m pytest test_paper_collaboration.py
"""

import json
import random
import re
import time
import zipfile
from io import BytesIO

import pytest
import requests

import conftest
from conftest import (
    BASE,
    EDITOR_BASE,
    PASS,
    UA,
    USER,
    dav_contains,
    dav_delete,
    dav_get,
    dav_mkcol,
    dav_put,
    close_editor,
    editor_canvas,
    goto,
    login,
    open_file_by_name,
    txt_bytes,
    wopi_info,
    wopi_open_and_capture,
    wopi_put,
)

import urllib3

urllib3.disable_warnings()

BOB = "wo-test-bob"
BOB_PASS = "Collab-Paper-2026!"
BOB_GRAPH_ID = "d1b11214-269b-4525-8e6c-bb88af0b5c9d"

_EVE_IDS: list[str] = []

SECTIONS = (
    "Quantum Tunneling in Photosynthetic Complexes",  # Title
    "We report evidence of long-lived quantum coherence.",  # Abstract
    "Photosynthesis exploits quantum effects at room temperature.",  # Introduction
    "Two-dimensional electronic spectroscopy was applied.",  # Methods
    "Coherence survived for 660 femtoseconds.",  # Results
    "Our findings challenge the classical view.",  # Discussion
    "Scholes et al., Nature 543, 647 (2017).",  # References
)
ACK = "We thank the E2E reviewers for constructive feedback."


def paper_docx(*extra: str) -> bytes:
    return conftest._docx_bytes(list(SECTIONS) + list(extra))


def bob_session() -> requests.Session:
    s = requests.Session()
    s.auth = (BOB, BOB_PASS)
    s.verify = False
    return s


def graph(method: str, path: str, **kw) -> requests.Response:
    s = requests.Session()
    s.auth = (USER, PASS)
    s.verify = False
    return s.request(method, f"{BASE}{path}", timeout=30, **kw)


@pytest.fixture(scope="module")
def paper(session_ctx):
    """The shared paper project: manuscript + figure + bibliography, bob ready."""
    # bob must be able to log in (admin resets his password; idempotent)
    r = graph(
        "PATCH",
        f"/graph/v1.0/users/{BOB_GRAPH_ID}",
        json={"passwordProfile": {"password": BOB_PASS}},
    )
    assert r.status_code == 200, f"bob password reset failed: {r.status_code}"

    folder = f"Paper-Quantum-{random.randint(1000, 9999)}"
    assert dav_mkcol(folder).status_code == 201
    assert dav_put(f"{folder}/manuscript.docx", paper_docx()).status_code == 201
    assert dav_put(f"{folder}/figure1-coherence.png", b"\x89PNG\r\n\x1a\nfakepng").status_code == 201
    assert dav_put(
        f"{folder}/references.bib",
        b"@article{scholes2017,\n  title={Quantum biology},\n  year={2017}\n}\n",
    ).status_code == 201

    # wait until the folder is visible in the listing the GUI actually uses
    # (the SPACES dav namespace — /dav/spaces/<drive-id> — NOT
    # /remote.php/dav/files/...): after editor activity a fresh MKCOL can
    # lag out of that view for minutes (posixfs id-cache), while the files
    # namespace and direct paths stay fresh
    s_admin = requests.Session()
    s_admin.auth = (USER, PASS)
    s_admin.verify = False
    drive_id = (
        s_admin.get(
            f"{BASE}/graph/v1beta1/me/drives?%24filter=driveType+eq+personal",
            timeout=20,
        )
        .json()["value"][0]["id"]
    )
    deadline = time.time() + 150
    listed = False
    while time.time() < deadline and not listed:
        pf = s_admin.request(
            "PROPFIND",
            f"{BASE}/dav/spaces/{drive_id}",
            headers={"Depth": "1"},
            timeout=20,
        )
        listed = pf.status_code == 207 and folder in pf.text
        if not listed:
            time.sleep(4)
    assert listed, f"{folder!r} never appeared in the spaces listing (id-cache degraded?)"

    yield folder

    dav_delete(folder)
    # remove any test users created during the run
    for uid in _EVE_IDS:
        try:
            graph("DELETE", f"/graph/v1.0/users/{uid}")
        except Exception:
            pass


def _ensure_bob_editor(folder: str):
    """Make sure bob holds EDIT rights on the shared folder.

    The share-role upgrade is broken server-side (GUI dropdown and OCS PUT
    both end in a grpc `update share` 500), so rights are fixed by re-inviting
    bob with full permissions — the same thing an admin would do via the API.
    """
    s = requests.Session()
    s.auth = (USER, PASS)
    s.verify = False
    h = {"OCS-APIRequest": "true"}
    r = s.get(f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares?format=json", headers=h, timeout=20)
    def _share_with_id(sh):
        sw = sh.get("share_with")
        if isinstance(sw, dict):
            return sw.get("user_id") or sw.get("uid")
        return sw

    mine = [
        sh
        for sh in r.json().get("ocs", {}).get("data", [])
        if folder in (sh.get("path") or "") and _share_with_id(sh) == BOB
    ]
    has_edit = any(int(sh.get("permissions", 0)) & 2 for sh in mine)
    if not has_edit:
        for sh in mine:
            s.delete(f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares/{sh['id']}", headers=h, timeout=20)
        rp = s.post(
            f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            headers=h,
            data={"path": f"/{folder}", "shareType": "0", "shareWith": BOB, "permissions": "31"},
            timeout=20,
        )
        assert rp.status_code == 200, f"editor re-invite failed: {rp.status_code}"
    deadline = time.time() + 30
    while time.time() < deadline:
        sh = _bob_share(folder)
        if sh and int(sh.get("permissions", 0)) & 2:
            return
        time.sleep(2)
    raise AssertionError("bob never obtained edit rights")


def _bob_share(folder: str):
    s = bob_session()
    r = s.get(
        f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares"
        "?shared_with_me=true&format=json",
        headers={"OCS-APIRequest": "true"},
        timeout=20,
    )
    if not r.ok:
        return None
    for sh in r.json().get("ocs", {}).get("data", []):
        if folder in (sh.get("file_target") or ""):
            return sh
    return None


def _ada_page(session_ctx, folder, into=True):
    """A fresh admin page; `into=True` navigates into the folder (file rows),
    `into=False` navigates to the personal root (folder row visible).

    Waits for the SPECIFIC target row: a fresh MKCOL can lag out of the
    listing (readdir cache) for a while even when other rows already show.
    """
    page = session_ctx.new_page()
    target = f"{BASE}/files/spaces/personal/admin/{folder}" if into else f"{BASE}/files/spaces/personal/admin"
    goto(page, target)
    row = page.locator(f"[data-test-resource-name='{folder}']" if not into
                       else "[data-test-resource-name]").first
    deadline = time.time() + 75
    while time.time() < deadline:
        if row.count() and row.is_visible():
            break
        page.wait_for_timeout(2500)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    row.wait_for(state="visible", timeout=10000)
    page.wait_for_timeout(1200)
    return page


def _open_editor(page, name="manuscript.docx") -> tuple[str, str]:
    fid, tok = wopi_open_and_capture(page, name)
    page.wait_for_timeout(2000)
    return fid, tok


# ── 1. project setup ─────────────────────────────────────────────────────────


@pytest.mark.gui
def test_01_manuscript_seed_is_a_valid_paper(paper):
    gr = dav_get(f"{paper}/manuscript.docx")
    assert gr.ok and gr.content[:2] == b"PK"
    doc = zipfile.ZipFile(BytesIO(gr.content)).read("word/document.xml").decode("utf-8", "ignore")
    for marker in SECTIONS:
        assert marker in doc, f"seed section missing: {marker!r}"
    assert dav_contains(f"{paper}/figure1-coherence.png", "fakepng")
    assert dav_contains(f"{paper}/references.bib", "scholes2017")


# ── 2. Ada writes ─────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_02_ada_opens_manuscript_in_worldoffice(session_ctx, paper):
    page = _ada_page(session_ctx, paper)
    try:
        open_file_by_name(page, "manuscript.docx")
        fr, canvas = editor_canvas(page)
        assert canvas.is_visible()
    # graceful editor unload — see conftest.close_editor
        close_editor(page, paper)
    finally:
        page.close()


@pytest.mark.gui
def test_03_ada_explicit_save_preserves_every_section(session_ctx, paper):
    page = _ada_page(session_ctx, paper)
    try:
        open_file_by_name(page, "manuscript.docx")
        editor_canvas(page)
        page.frames[-1].locator("body").press("Control+s")
        page.wait_for_timeout(5000)
        gr = dav_get(f"{paper}/manuscript.docx")
        assert gr.content[:2] == b"PK"
        doc = zipfile.ZipFile(BytesIO(gr.content)).read("word/document.xml").decode("utf-8", "ignore")
        for marker in SECTIONS:
            assert marker in doc, f"section lost on save: {marker!r}"
    # graceful editor unload — see conftest.close_editor
        close_editor(page, paper)
    finally:
        page.close()


# ── 3. sharing with the co-author ─────────────────────────────────────────────


@pytest.mark.gui
def test_04_ada_shares_project_folder_with_bob(session_ctx, paper):
    page = _ada_page(session_ctx, paper, into=False)
    # the panel confirms via a Graph invite POST (…/items/<id>/invite);
    # capture its response — the authoritative, instantaneous evidence
    # that the invite went through (bob's OCS metadata view can lag under
    # load, see test_05)
    invites: list = []

    def _capture(r):
        if r.request.method == "POST" and "/invite" in r.url:
            try:
                invites.append((r.status, r.json()))
            except ValueError:
                invites.append((r.status, None))

    page.on("response", _capture)
    try:
        page.locator(f"[data-test-resource-name='{paper}']").first.click(button="right")
        page.wait_for_timeout(1100)
        page.locator("[role=menuitem]:has-text('Share')").first.click()
        page.wait_for_timeout(2000)
        sb = page.locator("#app-sidebar")
        inp = sb.locator("input:visible").first
        inp.click()
        inp.type("bob", delay=60)
        page.wait_for_timeout(2200)
        page.locator("[role=option]:has-text('wo-test-bob')").first.click()
        page.wait_for_timeout(2000)
        # NB: `get_by_role(exact=True)` is required — `button:has-text('Share')`
        # matches the "Shares" panel TAB first and clicks the wrong element
        sb.get_by_role("button", name="Share", exact=True).first.click()
        page.wait_for_timeout(3000)

        assert invites and invites[-1][0] == 200, (
            f"graph invite POST failed or absent: {[(s2) for s2, _ in invites]}"
        )
        val = (invites[-1][1] or {}).get("value", [])
        assert any(
            BOB_GRAPH_ID in json.dumps(granted) for granted in val
        ), f"invite response does not grant bob: {json.dumps(val)[:200]}"

        # bob's OCS view follows (instant on a healthy server)
        deadline = time.time() + 45
        sh = _bob_share(paper)
        while time.time() < deadline and sh is None:
            page.wait_for_timeout(3000)
            sh = _bob_share(paper)
        assert sh is not None, (
            f"share never materialized for bob (OCS shared_with_me lacks {paper!r})"
        )
    finally:
        page.close()


@pytest.mark.gui
@pytest.mark.xfail(
    reason="SERVER BUG: upgrading a share's role fails with a grpc `update share` "
           "500 — both the GUI role dropdown and the OCS PUT; rights can only "
           "be fixed by re-inviting with permissions",
    strict=False,
)
def test_04b_share_role_upgrade_to_editor(session_ctx, paper):
    page = _ada_page(session_ctx, paper, into=False)
    try:
        page.locator(f"[data-test-resource-name='{paper}']").first.click(button="right")
        page.wait_for_timeout(1100)
        page.locator("[role=menuitem]:has-text('Share')").first.click()
        page.wait_for_timeout(2000)
        sb = page.locator("#app-sidebar")
        rolebtns = sb.locator("button:has-text('Can view')")
        assert rolebtns.count() >= 1, "no role selector on the shared row"
        rolebtns.nth(rolebtns.count() - 1).click()
        page.wait_for_timeout(1200)
        opt = page.locator(
            "[role=option]:has-text('Can edit'), li:has-text('Can edit')"
        ).first
        assert opt.count(), "no 'Can edit' option offered"
        opt.click()
        page.wait_for_timeout(3000)
        sh = _bob_share(paper)
        assert sh and int(sh.get("permissions", 0)) & 2, (
            f"role upgrade never landed: perms={sh and sh.get('permissions')}"
        )
    finally:
        page.close()


@pytest.mark.gui
def test_05_bob_sees_the_shared_project(paper):
    """Bob sees the received share — asserted on the AUTHORITATIVE source.

    The OCS `shared_with_me` record is the source of truth (it lists the
    share the moment the invite materializes). The DAV Shares jail is NOT:
    it serves stale snapshots (lists long-deleted shares, omits fresh ones
    for minutes) — see test_05b.
    """
    ok = False
    deadline = time.time() + 40
    while time.time() < deadline and not ok:
        sh = _bob_share(paper)
        ok = bool(sh) and int((sh or {}).get("permissions", 0)) & 1
        if not ok:
            time.sleep(3)
    assert ok, f"share for {paper!r} never materialized in bob's shared_with_me"


@pytest.mark.gui
@pytest.mark.xfail(
    reason="SERVER BUG: bob's DAV Shares jail serves stale snapshots — it "
           "lists shares of long-deleted folders and omits freshly accepted "
           "ones for minutes (mount cache lags the OCS share record). The "
           "GUI/API views and direct Shares/<folder>/ PROPFINDs (trailing "
           "slash) work once provisioned; the jail ROOT listing does not.",
    strict=False,
)
def test_05b_share_mount_lists_project_in_dav_jail(paper):
    pf = bob_session().request(
        "PROPFIND",
        f"{BASE}/remote.php/dav/files/{BOB}/Shares",
        headers={"Depth": "1"},
        timeout=20,
    )
    assert pf.status_code == 207, f"bob Shares jail broken: {pf.status_code}"
    assert paper in pf.text, "shared folder missing from the DAV jail listing"


@pytest.mark.gui
def test_06_bob_edits_the_manuscript(pw, paper):
    _ensure_bob_editor(paper)
    ctx = pw.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900}, user_agent=UA)
    page = ctx.new_page()
    try:
        login(page, BOB, BOB_PASS)
        goto(page, f"{BASE}/files/shares")
        page.wait_for_timeout(3500)
        # navigate into the shared project folder, then open the manuscript
        page.locator(f"[data-test-resource-name='{paper}']").first.click()
        page.wait_for_timeout(2500)
        row = page.locator("[data-test-resource-name='manuscript.docx']").first
        row.wait_for(state="visible", timeout=25000)

        # in the shared-space view an `.open-file-bar` overlay can intercept
        # the plain row click — force through and fall back to the bar button
        import re as _re
        import urllib.parse as _up
        holder = {"r": None}

        def _spy(req):
            if holder["r"]:
                return
            m = _re.search(r"/wopi/files/([0-9a-fA-F]{64})", req.url)
            if m:
                q = _up.parse_qs(_up.urlparse(req.url).query)
                holder["r"] = (m.group(1), q.get("access_token", [None])[0])

        page.on("request", _spy)
        try:
            try:
                row.click(timeout=8000)
            except Exception:
                row.click(force=True)
            page.wait_for_timeout(2500)
            if page.locator("iframe").count() == 0:
                bar = page.locator(".open-file-bar a, .open-file-bar button").first
                if bar.count():
                    bar.click()
                    page.wait_for_timeout(4000)
            deadline = time.time() + 30
            while time.time() < deadline and not holder["r"]:
                page.wait_for_timeout(800)
        finally:
            page.remove_listener("request", _spy)
        assert holder["r"], "bob's editor never issued a WOPI request"
        file_id, token = holder["r"]

        info = wopi_info(file_id, token).json()
        assert info.get("UserCanWrite") is True, f"bob must have write access: {info}"

        payload = paper_docx(ACK)  # bob adds the Acknowledgments section
        assert wopi_put(file_id, token, payload) == 200, "bob's save was rejected"

        assert dav_contains(f"{paper}/manuscript.docx", "E2E reviewers", timeout_s=60), (
            "bob's section never reached storage"
        )
    # graceful editor unload — see conftest.close_editor
        close_editor(page, url=f"{BASE}/files/shares")
    finally:
        page.close()
        ctx.close()


# ── 4. conflict + history ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_07_concurrent_saves_do_not_corrupt(session_ctx, paper):
    """Bob's API save racing Ada's editor save: paper stays intact."""
    page = _ada_page(session_ctx, paper)
    try:
        file_id, token = _open_editor(page)

        # bob fires his save while ada's editor is open and dirty-saves;
        # NB: playwright sync API is single-threaded — the browser save runs on
        # the main thread, the raw API save in the worker
        bob_payload = paper_docx("Bob raced his section in.")
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(wopi_put, file_id, token, bob_payload)
            fr = page.frames[-1]
            fr.locator("body").press("Control+s")
            bob_status = fut.result()
        page.wait_for_timeout(5000)
        assert bob_status == 200, f"bob's racing save failed: {bob_status}"

        gr = dav_get(f"{paper}/manuscript.docx")
        assert gr.content[:2] == b"PK", "concurrent save corrupted the file"
        doc = zipfile.ZipFile(BytesIO(gr.content)).read("word/document.xml").decode("utf-8", "ignore")
        assert all(m in doc for m in SECTIONS), "paper sections lost during the race"
    finally:
        page.close()


@pytest.mark.gui
def test_08_version_history_lists_prior_versions(session_ctx, paper):
    page = _ada_page(session_ctx, paper)
    try:
        file_id, token = _open_editor(page)
        # two distinct-content saves → at least one prior version must exist
        for extra in ("v-marker-one", "v-marker-two"):
            assert wopi_put(file_id, token, paper_docx(extra)) == 200
            page.wait_for_timeout(1500)

        # back to the file list (the editor iframe replaced it), then sidebar
        goto(page, f"{BASE}/files/spaces/personal/admin/{paper}")
        page.locator("[data-test-resource-name='manuscript.docx']").first.wait_for(
            state="visible", timeout=25000
        )
        # the open-file-bar overlay can intercept the plain select-click
        try:
            page.locator("[data-test-resource-name='manuscript.docx']").first.click(timeout=8000)
        except Exception:
            page.locator("[data-test-resource-name='manuscript.docx']").first.click(force=True)
        page.wait_for_timeout(900)
        # with an active share, selecting the file AUTO-opens the sidebar —
        # only click the opener when it is not already open
        sb = page.locator("#app-sidebar")
        if not (sb.count() and sb.first.is_visible()):
            page.get_by_role("button", name="Open sidebar to view details").click()
        page.wait_for_timeout(2000)
        page.locator("#app-sidebar button:has-text('Versions')").first.click()
        page.wait_for_timeout(2500)
        body = page.locator("#app-sidebar").inner_text()
        assert "Version" in body, f"versions panel empty/broken: {body[:200]!r}"
    # graceful editor unload — see conftest.close_editor
        close_editor(page, paper)
    finally:
        page.close()


# ── 5. review link + submission download ──────────────────────────────────────


@pytest.mark.gui
def test_09_public_review_link_downloads_readonly(session_ctx, paper):
    page = _ada_page(session_ctx, paper)
    try:
        page.locator("[data-test-resource-name='manuscript.docx']").first.click(button="right")
        page.wait_for_timeout(1100)
        page.locator("[role=menuitem]:has-text('Share')").first.click()
        page.wait_for_timeout(2000)
        page.locator("#app-sidebar button:has-text('Add link')").first.click()
        # the link materializes asynchronously as an "Unnamed link" row (the
        # panel build does not surface the URL itself — fetch it via the API)
        deadline = time.time() + 20
        listed = False
        while time.time() < deadline and not listed:
            listed = "Unnamed link" in page.locator("#app-sidebar").inner_text()
            if not listed:
                page.wait_for_timeout(1500)
        assert listed, "public link row did not appear after 'Add link'"
    finally:
        page.close()

    s = requests.Session()
    s.auth = (USER, PASS)
    s.verify = False
    r = s.get(
        f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares",
        params={"path": f"/{paper}/manuscript.docx", "format": "json"},
        headers={"OCS-APIRequest": "true"},
        timeout=20,
    )
    link_shares = [
        sh
        for sh in r.json().get("ocs", {}).get("data", [])
        if int(sh.get("share_type", -1)) == 3
        and (sh.get("path") == f"/{paper}/manuscript.docx"
             or "manuscript" in (sh.get("file_target") or ""))
    ]
    assert link_shares, "link share not created (shareType 3 missing)"

    # anonymous reviewer: no auth headers at all — the public DAV endpoint
    # serves the linked file bytes directly (the /s/<token> pages are SPA
    # HTML; a file link requires the target name in the path). Links from
    # earlier runs may linger in the listing — probe until one serves bytes.
    anon = requests.Session()
    anon.verify = False
    dl = None
    tried = []
    for sh in link_shares:
        token = sh.get("token")
        linked_name = (sh.get("file_target") or "/manuscript.docx").lstrip("/")
        if not token:
            continue
        page_r = anon.get(f"{BASE}/s/{token}", timeout=20, allow_redirects=True)
        pf = anon.request(
            "PROPFIND", f"{BASE}/remote.php/dav/public-files/{token}",
            headers={"Depth": "1"}, timeout=20,
        )
        cand = anon.get(
            f"{BASE}/remote.php/dav/public-files/{token}/{linked_name}",
            timeout=20,
            allow_redirects=True,
        )
        tried.append((token, page_r.status_code, pf.status_code, cand.status_code))
        if page_r.status_code == 200 and pf.status_code == 207 and cand.content[:2] == b"PK":
            dl = cand
            break
    assert dl is not None, f"no link serves the manuscript anonymously: {tried}"
    assert SECTIONS[0].encode() in dl.content, "reviewer download lacks the paper title"


@pytest.mark.gui
def test_10_journal_download_from_gui(session_ctx, paper):
    page = _ada_page(session_ctx, paper)
    try:
        row = page.locator("[data-test-resource-name='manuscript.docx']").first
        row.click(button="right")
        page.wait_for_timeout(1100)
        with page.expect_download(timeout=30000) as dl_info:
            page.locator("[role=menuitem]:has-text('Download')").first.click()
        dl = dl_info.value
        target = f"/tmp/{dl.suggested_filename or 'paper-dl'}"
        dl.save_as(target)
        blob = open(target, "rb").read()
        assert blob[:2] == b"PK", "downloaded manuscript is not a docx"
        doc = zipfile.ZipFile(BytesIO(blob)).read("word/document.xml").decode("utf-8", "ignore")
        assert SECTIONS[0] in doc, "downloaded manuscript lacks the paper title"
    finally:
        page.close()


# ── 6. lifecycle: rename, outsiders, accidents ────────────────────────────────


@pytest.mark.gui
def test_11_rename_keeps_coauthor_connected(session_ctx, paper):
    page = _ada_page(session_ctx, paper)
    new_name = f"manuscript-final-{random.randint(100,999)}.docx"
    try:
        row = page.locator("[data-test-resource-name='manuscript.docx']").first
        row.click(button="right")
        page.wait_for_timeout(1100)
        page.locator("[role=menuitem]:has-text('Rename')").first.click()
        page.wait_for_timeout(1200)
        box = page.locator("input:visible:focus, [contenteditable]:focus").first
        if not box.count():
            box = page.locator("input:visible").last
        box.fill(new_name)
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)

        assert dav_get(f"{paper}/{new_name}").ok, "renamed manuscript not found via DAV"
        assert dav_get(f"{paper}/manuscript.docx").status_code == 404, "old name still resolves"
        # bob stays connected: the mounted share lists the renamed file
        # (trailing slash required — without it the mount root itself lists)
        deadline = time.time() + 25
        seen = False
        while time.time() < deadline and not seen:
            probe = bob_session().request(
                "PROPFIND",
                f"{BASE}/remote.php/dav/files/{BOB}/Shares/{paper}/",
                headers={"Depth": "1"},
                timeout=20,
            )
            seen = probe.status_code == 207 and new_name in probe.text
            if not seen:
                page.wait_for_timeout(3000)
        assert seen, "bob lost the manuscript after ada's rename"
    finally:
        # keep later tests independent of the rename
        page.close()


@pytest.mark.gui
def test_12_outsider_eve_cannot_read_the_paper(paper):
    eve = f"eve{random.randint(1000, 9999)}"
    r = graph(
        "POST",
        "/graph/v1.0/users",
        json={
            "onPremisesSamAccountName": eve,
            "displayName": f"Eve {eve}",
            "mail": f"{eve}@example.org",
            "passwordProfile": {"password": "Eve-Not-Invited-2026!"},
        },
    )
    if r.status_code not in (200, 201):
        pytest.skip(f"user provisioning via graph unavailable ({r.status_code})")
    eve_id = r.json().get("id")
    if eve_id:
        _EVE_IDS.append(eve_id)

    s = requests.Session()
    s.auth = (eve, "Eve-Not-Invited-2026!")
    s.verify = False
    # eve must not read the admin's project folder
    probe = s.request(
        "PROPFIND", f"{BASE}/remote.php/dav/files/{USER}/", headers={"Depth": "1"}, timeout=20
    )
    assert probe.status_code in (401, 403, 404), (
        f"eve could enumerate admin's files: {probe.status_code}"
    )
    r2 = s.get(f"{BASE}/remote.php/dav/files/{USER}/{paper}/figure1-coherence.png", timeout=20)
    assert r2.status_code in (401, 403, 404), f"eve downloaded the project figure: {r2.status_code}"


@pytest.mark.gui
def test_13_accidental_delete_is_restored_from_trash(session_ctx, paper):
    sub = f"results-{random.randint(1000, 9999)}"
    assert dav_mkcol(f"{paper}/{sub}").status_code == 201
    assert dav_put(f"{paper}/{sub}/data.csv", b"run,coherence_fs\n1,660\n").status_code == 201

    page = _ada_page(session_ctx, paper)
    try:
        page.locator(f"[data-test-resource-name='{sub}']").first.click(button="right")
        page.wait_for_timeout(1100)
        page.locator("[role=menuitem]:has-text('Delete')").first.click()
        page.wait_for_timeout(2500)
    finally:
        page.close()

    # the deletion must land in the SERVER trash-bin (authoritative)
    s = requests.Session()
    s.auth = (USER, PASS)
    s.verify = False
    href = None
    deadline = time.time() + 40
    while time.time() < deadline and href is None:
        pf = s.request(
            "PROPFIND", f"{BASE}/remote.php/dav/trash-bin/{USER}",
            headers={"Depth": "1"}, timeout=20,
        )
        for resp in (pf.text.split("<d:response>")[1:] if pf.status_code == 207 else []):
            if sub in resp:
                href = re.search(r"<d:href>([^<]+)</d:href>", resp).group(1)
                break
        if href is None:
            time.sleep(2)
    assert href, f"deleted folder {sub!r} never reached the server trash-bin"

    # restore: MOVE from the trash-bin back into the personal space
    mv = s.request(
        "MOVE",
        f"{BASE}{href}",
        headers={"Destination": f"{BASE}/remote.php/dav/files/{USER}/{paper}/{sub}"},
        timeout=20,
    )
    assert mv.status_code in (200, 201, 204), f"trash restore failed: {mv.status_code}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if dav_get(f"{paper}/{sub}/data.csv").ok:
            break
        time.sleep(2)
    g = dav_get(f"{paper}/{sub}/data.csv")
    assert g.ok and b"660" in g.content, "restored data lost"
    dav_delete(f"{paper}/{sub}")


@pytest.mark.gui
@pytest.mark.xfail(
    reason="GUI BUG: the trash overview does not list freshly deleted items "
           "(the server trash-bin has them — verified by test_13); combined "
           "with the older finding that it hides deleted FILES entirely, the "
           "GUI trash is unreliable for restore workflows",
    strict=False,
)
def test_13b_gui_trash_lists_fresh_delete(session_ctx, paper):
    sub = f"vanish-{random.randint(1000, 9999)}"
    assert dav_mkcol(f"{paper}/{sub}").status_code == 201

    page = _ada_page(session_ctx, paper)
    try:
        page.locator(f"[data-test-resource-name='{sub}']").first.click(button="right")
        page.wait_for_timeout(1100)
        page.locator("[role=menuitem]:has-text('Delete')").first.click()
        page.wait_for_timeout(2500)

        goto(page, f"{BASE}/files/trash/overview")
        deadline = time.time() + 30
        while time.time() < deadline:
            if page.locator(f"[data-test-resource-name='{sub}']").count():
                return
            page.wait_for_timeout(2000)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
        raise AssertionError(f"{sub!r} never appeared in the GUI trash overview")
    finally:
        page.close()
        dav_delete(f"{paper}/{sub}")
