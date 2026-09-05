"""Editor-opening tests: every office type must open an editor via the GUI.

docx opens the World-Office WASM canvas directly (hard requirement).
odt is advertised in WOPI discovery but the GUI currently shows an
"open with" bar — tracked as an app-registration gap (xfail).
ods/odp are not advertised at all — OnlyOffice parity gap (xfail).
txt/pdf must open *something* (WOPI editor or a native OCIS surface).
"""

import random

import pytest

from conftest import (
    dav_delete,
    dav_put,
    docx_bytes,
    editor_canvas,
    editor_frame,
    open_file_by_name,
    pdf_bytes,
    txt_bytes,
)


def _assert_any_surface(page, name):
    """WOPI editor iframe, OCIS text editor, or a viewer — anything but a dead click."""
    if page.query_selector("iframe") is not None:
        return
    native = page.locator(
        "textarea:visible, .monaco-editor:visible, [contenteditable='true']:visible, "
        ".text-editor:visible, .pdf-viewer:visible, canvas:visible"
    )
    if native.count() == 0:
        pytest.fail(f"{name}: clicking the file opened no editor or viewer at all")


def _assert_wopi_editor(page):
    fr = editor_frame(page)
    assert fr is not None, "editor iframe did not load"
    assert fr.locator("canvas").count() >= 1, "no canvas inside the WOPI editor"


def _create_via_gui(page, ext: str, label: str, name: str):
    page.get_by_role("button", name="New", exact=True).click()
    page.wait_for_timeout(1000)
    page.locator(f"[role=menuitem]:has-text({label!r})").last.click()
    page.wait_for_timeout(1000)
    page.locator("input[type=text]").last.fill(name)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)


@pytest.mark.gui
def test_open_docx_word_editor(in_run_folder, run_id):
    name = f"e2e-open-{random.randint(1000, 9999)}.docx"
    r = dav_put(f"{run_id}/{name}", docx_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    open_file_by_name(in_run_folder, name)
    _assert_wopi_editor(in_run_folder)
    fr, canvas = editor_canvas(in_run_folder)
    assert canvas.is_visible(), "word editor canvas not visible"
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
@pytest.mark.xfail(
    reason="odt is advertised in WOPI discovery but the GUI opens an "
    "'open with' bar instead of the editor — app registration gap",
    strict=False,
)
def test_open_odt_via_gui_created_template(in_run_folder, run_id):
    name = f"e2e-odt-{random.randint(1000, 9999)}.odt"
    _create_via_gui(in_run_folder, "odt", "Document", name)
    assert in_run_folder.locator(f"[data-test-resource-name={name!r}]").count() >= 1
    open_file_by_name(in_run_folder, name)
    _assert_wopi_editor(in_run_folder)
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
@pytest.mark.xfail(
    reason="PARITY GAP: docserver WOPI discovery advertises word docs only — "
    "spreadsheets cannot open in the World-Office editor (OnlyOffice parity target)",
    strict=False,
)
def test_open_ods_spreadsheet_editor(in_run_folder, run_id):
    name = f"e2e-ods-{random.randint(1000, 9999)}.ods"
    _create_via_gui(in_run_folder, "ods", "Spreadsheet", name)
    assert in_run_folder.locator(f"[data-test-resource-name={name!r}]").count() >= 1
    open_file_by_name(in_run_folder, name)
    _assert_wopi_editor(in_run_folder)
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
@pytest.mark.xfail(
    reason="PARITY GAP: docserver WOPI discovery advertises word docs only — "
    "presentations cannot open in the World-Office editor (OnlyOffice parity target)",
    strict=False,
)
def test_open_odp_presentation_editor(in_run_folder, run_id):
    name = f"e2e-odp-{random.randint(1000, 9999)}.odp"
    _create_via_gui(in_run_folder, "odp", "Presentation", name)
    assert in_run_folder.locator(f"[data-test-resource-name={name!r}]").count() >= 1
    open_file_by_name(in_run_folder, name)
    _assert_wopi_editor(in_run_folder)
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_open_txt(in_run_folder, run_id):
    name = f"e2e-txt-{random.randint(1000, 9999)}.txt"
    r = dav_put(f"{run_id}/{name}", txt_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    open_file_by_name(in_run_folder, name)
    _assert_any_surface(in_run_folder, name)
    dav_delete(f"{run_id}/{name}")


@pytest.mark.gui
def test_open_pdf(in_run_folder, run_id):
    name = f"e2e-pdf-{random.randint(1000, 9999)}.pdf"
    r = dav_put(f"{run_id}/{name}", pdf_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    open_file_by_name(in_run_folder, name)
    _assert_any_surface(in_run_folder, name)
    dav_delete(f"{run_id}/{name}")
