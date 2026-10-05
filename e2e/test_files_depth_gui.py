"""Files-app depth: search, details sidebar, view modes, trash, open-with."""

import os
import random

import pytest

from conftest import (
    BASE,
    USER,
    dav_delete,
    dav_put,
    docx_bytes,
    find_btn,
    goto,
    txt_bytes,
)


def _seed(in_run_folder, run_id, ext="txt", content=None) -> tuple[object, str]:
    name = f"e2e-fd-{random.randint(1000, 9999)}.{ext}"
    data = content if content is not None else txt_bytes()
    r = dav_put(f"{run_id}/{name}", data)
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    return in_run_folder, name


def _trash_propfind_contains(name: str) -> bool:
    import requests
    import urllib3

    urllib3.disable_warnings()
    sess = requests.Session()
    sess.auth = (USER, os.environ.get("E2E_PASS", "wo-od-2026"))
    sess.verify = False
    r = sess.request(
        "PROPFIND", f"{BASE}/remote.php/dav/trash-bin/{USER}",
        headers={"Depth": "1"}, timeout=30,
    )
    return r.status_code == 207 and name in r.text


@pytest.mark.gui
def test_search_finds_file(in_run_folder, run_id):
    page, name = _seed(in_run_folder, run_id)
    btn = find_btn(page, "Search")
    assert btn is not None, "search button not found"
    btn.click()
    page.wait_for_timeout(1500)
    inp = page.locator("input[type=search]:visible").first
    if inp.count() == 0:
        pytest.skip("search input did not open")
    inp.fill(name)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3500)
    results = page.locator(f"[data-test-resource-name]:has-text({name!r})")
    assert results.count() >= 1, f"search did not surface {name}"
    # reset: the search overlay replaces the file list view
    goto(page, f"{BASE}/files/spaces/personal/admin/{run_id}")
    page.wait_for_timeout(2500)
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_details_sidebar_shows_file_info(in_run_folder, run_id):
    page, name = _seed(in_run_folder, run_id)
    page.locator(f"[data-test-resource-name={name!r}]").first.click()
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="Open sidebar to view details").click()
    page.wait_for_timeout(2500)
    panel = page.locator("#app-sidebar")
    assert panel.count() >= 1 and panel.is_visible(), "details sidebar did not open"
    assert name.split(".")[0][:8] in (panel.inner_text() or ""), (
        "sidebar does not show the file's name"
    )
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_switch_view_mode(in_run_folder, run_id):
    page, name = _seed(in_run_folder, run_id)
    page.get_by_role("button", name="Switch view mode").click()
    page.wait_for_timeout(2000)
    assert page.locator(f"[data-test-resource-name={name!r}]").count() >= 1, (
        "file lost after view-mode switch"
    )
    # restore the view: the mode persists as a server-side user preference —
    # leaving it flipped poisons every later GUI module (the tiles context
    # menu lacks e.g. 'Rename', which breaks downstream right-click tests)
    page.get_by_role("button", name="Switch view mode").click()
    page.wait_for_timeout(2000)
    assert page.locator(f"[data-test-resource-name={name!r}]").count() >= 1, (
        "file lost after view-mode restore"
    )
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_deleted_file_reaches_server_trash(in_run_folder, run_id):
    """GUI delete must move the file into the server-side trash-bin."""
    page, name = _seed(in_run_folder, run_id)
    row = page.locator(f"[data-test-resource-name={name!r}]").first
    row.click(button="right")
    page.wait_for_timeout(1200)
    page.locator("[role=menuitem]:has-text('Delete')").first.click()
    page.wait_for_timeout(3000)  # delete is immediate (Undo toast)
    assert page.locator(f"[data-test-resource-name={name!r}]").count() == 0, (
        "file still listed after delete"
    )
    assert _trash_propfind_contains(name), "deleted file missing from server trash-bin"


@pytest.mark.gui
@pytest.mark.xfail(
    reason="GUI BUG: the trash overview lists deleted folders but not deleted "
    "files, although the server trash-bin contains them (PROPFIND verified) — "
    "OpenCloud web rendering/filter issue",
    strict=False,
)
def test_deleted_file_visible_in_gui_trash(in_run_folder, run_id):
    page, name = _seed(in_run_folder, run_id)
    row = page.locator(f"[data-test-resource-name={name!r}]").first
    row.click(button="right")
    page.wait_for_timeout(1200)
    page.locator("[role=menuitem]:has-text('Delete')").first.click()
    page.wait_for_timeout(3000)
    goto(page, f"{BASE}/files/trash/overview")
    found = False
    for _ in range(4):
        page.wait_for_timeout(3500)
        if page.locator(f"[data-test-resource-name]:has-text({name[:12]!r})").count():
            found = True
            break
        page.reload()
    assert found, "deleted file not rendered in the GUI trash overview"


@pytest.mark.gui
def test_open_with_lists_worldoffice_for_docx(in_run_folder, run_id):
    page, name = _seed(in_run_folder, run_id, ext="docx", content=docx_bytes())
    row = page.locator(f"[data-test-resource-name={name!r}]").first
    row.click(button="right")
    page.wait_for_timeout(1200)
    page.locator("[role=menuitem]:has-text('Open with...')").first.click()
    page.wait_for_timeout(1500)
    apps = page.locator("[role=menuitem]:visible").all_inner_texts()
    assert any("world" in a.lower() or "office" in a.lower() for a in apps), (
        f"WorldOffice missing from 'Open with...' for docx: {apps}"
    )
    page.keyboard.press("Escape")
    dav_delete(f"{run_id}/{name}")
