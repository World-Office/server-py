"""Share lifecycle: grants, revocations, link protection, reshare control.

The collaboration scenario (test_paper_collaboration) proves bob can be
invited; this module proves the *lifecycle* — access appears, is revoked,
is restored, public links can be locked behind a password and restricted
to read-only, and a collaborator without the share bit cannot re-share.

Protocol-level (OCS + DAV) with GUI assertions only where the GUI is the
feature under test. Run the module as a whole:
    python3 -m pytest test_share_lifecycle.py
"""

import base64
import random
import time

import pytest
import requests

import conftest
from conftest import (
    BASE,
    docx_bytes,
    dav_contains,
    dav_delete,
    dav_get,
    dav_mkcol,
    dav_propfind,
    dav_put,
    goto,
    ocs_create_share,
    ocs_delete_share,
    ocs_shares,
)

import urllib3

urllib3.disable_warnings()

BOB = "wo-test-bob"
BOB_PASS = "Collab-Paper-2026!"
LINK_PASS = "Review-2026!"


def bob_session() -> requests.Session:
    s = requests.Session()
    s.auth = (BOB, BOB_PASS)
    s.verify = False
    return s


def _bob_sees(folder: str) -> bool:
    """True when bob's Shares mount lists the folder (trailing slash!)."""
    pf = bob_session().request(
        "PROPFIND",
        f"{BASE}/remote.php/dav/files/{BOB}/Shares/",
        headers={"Depth": "1"},
        timeout=20,
    )
    return pf.status_code == 207 and folder in pf.text


def _poll(fn, want, timeout_s: float = 30.0, interval: float = 2.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn() == want:
            return True
        time.sleep(interval)
    return fn() == want


@pytest.fixture(scope="module")
def project(session_ctx):
    """A project folder shared with bob (editor rights)."""
    folder = f"Shared-Project-{random.randint(1000, 9999)}"
    assert dav_mkcol(folder).status_code == 201
    assert dav_put(f"{folder}/notes.docx", docx_bytes()).status_code == 201
    status, _ = ocs_create_share(f"/{folder}", 0, shareWith=BOB, permissions="31")
    assert status == 200, f"bob invite failed: {status}"
    assert _poll(lambda: _bob_sees(folder), True), "bob never saw the share"

    yield folder

    dav_delete(folder)
    for sh in ocs_shares(path=f"/{folder}"):
        ocs_delete_share(sh.get("id"))


def _user_share(folder: str) -> dict | None:
    for sh in ocs_shares(path=f"/{folder}"):
        if int(sh.get("share_type", -1)) == 0:
            return sh
    return None


def _link_shares(folder: str, name: str | None = None) -> list[dict]:
    out = []
    for sh in ocs_shares(path=f"/{folder}" + (f"/{name}" if name else "")):
        if int(sh.get("share_type", -1)) == 3:
            out.append(sh)
    return out


def _anon_get_link(token: str, name: str, password: str | None = None):
    """Anonymous GET on a public-link file; optional public-link auth."""
    hdrs = {}
    if password is not None:
        # OCIS accepts the link credentials as basic auth on the public DAV
        hdrs["Authorization"] = "Basic " + base64.b64encode(
            f"public:{password}".encode()
        ).decode()
    return requests.get(
        f"{BASE}/remote.php/dav/public-files/{token}/{name}",
        headers=hdrs,
        timeout=20,
        allow_redirects=True,
        verify=False,
    )


# ── user share lifecycle ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_01_unshare_revokes_bob_immediately(project):
    share = _user_share(project)
    assert share, "no user share for bob found"
    assert ocs_delete_share(share["id"]) in (200, 204)
    assert _poll(lambda: _bob_sees(project), False, timeout_s=25), (
        "bob still sees the folder after the share was deleted"
    )
    # his mount path is gone entirely
    probe = bob_session().request(
        "PROPFIND",
        f"{BASE}/remote.php/dav/files/{BOB}/Shares/{project}/",
        headers={"Depth": "1"},
        timeout=20,
    )
    assert probe.status_code in (404, 403, 405), (
        f"bob can still PROPFIND the unshared folder: {probe.status_code}"
    )


@pytest.mark.gui
def test_02_reshare_restores_full_access(project):
    status, _ = ocs_create_share(f"/{project}", 0, shareWith=BOB, permissions="31")
    assert status == 200, f"re-invite failed: {status}"
    assert _poll(lambda: _bob_sees(project), True), "bob cannot see the re-shared folder"
    # and he can write through the mount again
    w = bob_session().put(
        f"{BASE}/remote.php/dav/files/{BOB}/Shares/{project}/bob-note.txt",
        data=b"restored access",
        timeout=20,
        verify=False,
    )
    assert w.status_code in (201, 204), f"bob write after re-share failed: {w.status_code}"
    assert dav_contains(f"{project}/bob-note.txt", "restored access")
    dav_delete(f"{project}/bob-note.txt")


@pytest.mark.gui
def test_03_collaborator_without_share_bit_cannot_reshare(project):
    """Bob holds permissions=15 (no share bit 16) → he must not be able to
    re-share the folder to a third user."""
    # rebuild bob's grant without the share bit (upgrade is broken server-side;
    # a fresh invite is the supported path — see conftest notes)
    share = _user_share(project)
    assert share, "no bob share"
    ocs_delete_share(share["id"])
    status, _ = ocs_create_share(f"/{project}", 0, shareWith=BOB, permissions="15")
    assert status == 200, f"re-invite without share bit failed: {status}"
    assert _poll(lambda: _bob_sees(project), True)

    # a disposable third user
    carol = f"carol{random.randint(1000, 9999)}"
    s = requests.Session()
    s.auth = (conftest.USER, conftest.PASS)
    s.verify = False
    r = s.post(
        f"{BASE}/graph/v1.0/users",
        json={
            "onPremisesSamAccountName": carol,
            "displayName": f"Carol {carol}",
            "mail": f"{carol}@example.org",
            "passwordProfile": {"password": "Carol-Third-2026!"},
        },
        timeout=30,
    )
    if r.status_code not in (200, 201):
        pytest.skip(f"user provisioning via graph unavailable ({r.status_code})")
    carol_id = r.json().get("id")
    try:
        rs = bob_session().post(
            f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares",
            data={
                "path": f"/Shares/{project}",
                "shareType": "0",
                "shareWith": carol,
                "permissions": "15",
            },
            headers={"OCS-APIRequest": "true"},
            timeout=20,
            verify=False,
        )
        assert rs.status_code in (403, 404), (
            f"bob re-shared without the share bit! ({rs.status_code})"
        )
    finally:
        if carol_id:
            s.delete(f"{BASE}/graph/v1.0/users/{carol_id}", timeout=20)


# ── public link lifecycle ────────────────────────────────────────────────────


@pytest.mark.gui
def test_04_password_link_blocks_anonymous(project):
    status, sh = ocs_create_share(
        f"/{project}/notes.docx", 3, permissions="1", password=LINK_PASS
    )
    assert status == 200, f"password link creation failed: {status}"
    token = sh.get("token")
    assert token, f"link share has no token: {sh}"

    # without credentials: rejected
    r0 = _anon_get_link(token, "notes.docx")
    assert r0.status_code in (401, 403), (
        f"anonymous download worked despite password! ({r0.status_code})"
    )
    # with the link password: the bytes flow
    r1 = _anon_get_link(token, "notes.docx", password=LINK_PASS)
    assert r1.status_code == 200, f"authenticated link download failed: {r1.status_code}"
    assert r1.content[:2] == b"PK", "link payload is not the docx"


@pytest.mark.gui
def test_05_readonly_link_forbids_write(project):
    status, sh = ocs_create_share(f"/{project}/notes.docx", 3, permissions="1")
    assert status == 200, f"read-only link creation failed: {status}"
    token = sh.get("token")
    assert token

    # read works (no password on this link)
    g = requests.get(
        f"{BASE}/remote.php/dav/public-files/{token}/notes.docx",
        timeout=20,
        verify=False,
    )
    assert g.status_code == 200, f"public read failed: {g.status_code}"

    # write must be refused
    for method, kw in (
        ("PUT", {"data": b"malicious overwrite"}),
        ("MKCOL", {}),
        ("PROPPATCH", {"headers": {"Overwrite": "F"}}),
    ):
        w = requests.request(
            method,
            f"{BASE}/remote.php/dav/public-files/{token}/evil.bin",
            timeout=20,
            verify=False,
            **kw,
        )
        assert w.status_code in (403, 404, 405, 409), (
            f"{method} on a read-only link unexpectedly returned {w.status_code}"
        )


@pytest.mark.gui
def test_06_deleted_link_stops_serving(project):
    status, sh = ocs_create_share(f"/{project}/notes.docx", 3, permissions="1")
    assert status == 200
    token = sh.get("token")
    assert token
    assert _anon_get_link(token, "notes.docx").status_code == 200

    assert ocs_delete_share(sh["id"]) in (200, 204)
    deadline = time.time() + 20
    code = 999
    while time.time() < deadline:
        code = _anon_get_link(token, "notes.docx").status_code
        if code in (404, 403, 410):
            break
        time.sleep(2)
    assert code in (404, 403, 410), f"revoked link still serves bytes ({code})"


# ── GUI: the share panel mirrors the API state ───────────────────────────────


@pytest.mark.gui
def test_07_share_panel_lists_bob(session_ctx, project):
    page = session_ctx.new_page()
    try:
        goto(page, f"{BASE}/files/spaces/personal/admin/{project}")
        page.locator("[data-test-resource-name]").first.wait_for(state="visible", timeout=25000)
        page.locator("[data-test-resource-name='notes.docx']").first.click(button="right")
        page.wait_for_timeout(1100)
        page.locator("[role=menuitem]:has-text('Share')").first.click()
        page.wait_for_timeout(2000)
        panel = page.locator("#app-sidebar")
        # bob (invited via API in the fixture) appears as a collaborator —
        # the panel renders his display name, not the account name
        deadline = time.time() + 20
        listed = False
        while time.time() < deadline and not listed:
            txt = panel.inner_text()
            listed = BOB in txt or "WO Test Bob" in txt
            if not listed:
                page.wait_for_timeout(1500)
        assert listed, f"share panel does not list bob (panel: {panel.inner_text()[:300]!r})"
    finally:
        page.close()
