"""Files-app GUI tests: create / rename / delete / navigate, plus WebDAV parity."""

import random

import pytest

from conftest import (
    dav_delete,
    dav_put,
    docx_bytes,
    pdf_bytes,
    txt_bytes,
)


@pytest.mark.gui
def test_new_folder_via_gui(in_run_folder, run_id):
    name = f"e2e-folder-{random.randint(1000, 9999)}"
    in_run_folder.get_by_role("button", name="New", exact=True).click()
    in_run_folder.wait_for_timeout(1000)
    in_run_folder.locator("#new-folder-btn").click()
    in_run_folder.wait_for_timeout(800)
    in_run_folder.locator("input[type=text]").last.fill(name)
    in_run_folder.keyboard.press("Enter")
    in_run_folder.wait_for_timeout(2500)
    row = in_run_folder.locator(f"[data-test-resource-name={name!r}]")
    assert row.count() >= 1, "created folder not visible in the file list"
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_new_document_via_gui(in_run_folder, run_id):
    """OCIS template creation: Document (odt) — must appear in the list."""
    name = f"e2e-doc-{random.randint(1000, 9999)}.odt"
    in_run_folder.get_by_role("button", name="New", exact=True).click()
    in_run_folder.wait_for_timeout(1000)
    in_run_folder.locator("[role=menuitem]:has-text('Document')").last.click()
    in_run_folder.wait_for_timeout(1200)
    inp = in_run_folder.locator("input[type=text]").last
    inp.fill(name)
    in_run_folder.keyboard.press("Enter")
    in_run_folder.wait_for_timeout(2500)
    row = in_run_folder.locator(f"[data-test-resource-name={name!r}]")
    assert row.count() >= 1, "created odt not visible in the file list"
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_uploaded_docx_is_listed(in_run_folder, run_id):
    name = f"e2e-up-{random.randint(1000, 9999)}.docx"
    r = dav_put(f"{run_id}/{name}", docx_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    row = in_run_folder.locator(f"[data-test-resource-name={name!r}]")
    assert row.count() >= 1, "WebDAV-uploaded docx not visible in the GUI"
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_rename_via_context_menu(in_run_folder, run_id):
    name = f"e2e-ren-{random.randint(1000, 9999)}.txt"
    r = dav_put(f"{run_id}/{name}", txt_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    row = in_run_folder.locator(f"[data-test-resource-name={name!r}]").first
    row.click(button="right")
    in_run_folder.wait_for_timeout(1500)
    in_run_folder.locator("[role=menuitem]:has-text('Rename')").first.click()
    in_run_folder.wait_for_timeout(1000)
    new_name = name.replace("ren-", "ren2-")
    inp = in_run_folder.locator("input[type=text]").last
    inp.fill(new_name)
    in_run_folder.keyboard.press("Enter")
    in_run_folder.wait_for_timeout(2500)
    assert (
        in_run_folder.locator(f"[data-test-resource-name={new_name!r}]").count() >= 1
    ), "renamed file not visible"
    dav_delete(f"{run_id}/{new_name}")


@pytest.mark.gui
def test_delete_via_context_menu(in_run_folder, run_id):
    name = f"e2e-del-{random.randint(1000, 9999)}.txt"
    r = dav_put(f"{run_id}/{name}", txt_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    row = in_run_folder.locator(f"[data-test-resource-name={name!r}]").first
    row.click(button="right")
    in_run_folder.wait_for_timeout(1500)
    in_run_folder.locator("[role=menuitem]:has-text('Delete')").first.click()
    in_run_folder.wait_for_timeout(3000)  # delete is immediate (Undo toast)
    assert in_run_folder.locator(f"[data-test-resource-name={name!r}]").count() == 0, (
        "deleted file still listed"
    )


@pytest.mark.gui
def test_folder_navigation(in_run_folder, run_id):
    sub = f"e2e-nav-{random.randint(1000, 9999)}"
    dav_mk = dav_put(f"{run_id}/{sub}/.keep", b"")
    # OCIS: PUT into a nonexistent collection may 404/409; create via MKCOL instead
    if dav_mk.status_code not in (201, 204):
        from conftest import _dav, dav_url

        _dav().request("MKCOL", dav_url(f"{run_id}/{sub}"), timeout=30)
        dav_put(f"{run_id}/{sub}/nested.txt", txt_bytes())
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    in_run_folder.locator(f"[data-test-resource-name={sub!r}]").first.click()
    in_run_folder.wait_for_timeout(3000)
    assert sub in in_run_folder.url, "clicking a folder must navigate into it"
    assert (
        in_run_folder.locator("[data-test-resource-name='nested.txt']").count() >= 1
    ), "file inside the folder not listed"
    dav_delete(f"{run_id}/{sub}")
