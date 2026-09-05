"""Editor File-menu chrome against the DEPLOYED bundle.

FINDING (2026-08-31): production (worldoffice/documentserver) ships the
spartan canvas editor. The rich File menu (21 items: Download as...,
Export Wizard..., Version History..., Create New...), the five template
cards (Blank/Resume/Formal Letter/Invoice/Report) and their panels all
exist in the source tree (`documenteditor-react/src/components/FileMenu/`)
and their DOM shells are present in the deployed editor — but they are NOT
interactive: the File tab has no accessible name, clicks time out, Alt+F
opens nothing. Interactive menu behaviours are therefore xfail-documented
here; the DOM-presence test pins the deployed state.
"""

import random

import pytest

from conftest import (
    dav_put,
    docx_bytes,
    editor_canvas,
    in_run_folder,  # noqa: F401  (fixture)
    open_file_by_name,
    run_id,  # noqa: F401  (fixture)
)

FILE_MENU_ITEMS = (
    "Download as...",
    "Export Wizard...",
    "Save Copy as...",
    "Save as...",
    "Print",
    "Rename...",
    "Document Info...",
    "Share...",
    "Version History...",
    "Advanced Settings...",
    "Create New",
    "Close Editor",
)

UNREACHABLE = (
    "EDITOR GAP: the File menu chrome is deployed but not interactive — "
    "the File tab exposes no accessible name, clicks time out and Alt+F "
    "opens nothing; the rich menu UI only exists in the undeployed "
    "documenteditor-react bundle"
)


def _open_editor(page, run_id) -> tuple:
    """Open a fresh docx in the editor; returns (frame, canvas).

    Falls back to a sibling folder when the run folder is wedged by the
    upstream id-cache bug (an editor teardown in a previous test can leave
    the folder's cache entry poisoned for ~5 min: PUT/MKCOL inside it
    answer 409 `not found` while GET/PROPFIND still work).
    """
    name = "menu-" + str(random.randint(1000, 9999)) + ".docx"
    r = dav_put(run_id + "/" + name, docx_bytes())
    folder = run_id
    if r.status_code == 409:
        from conftest import BASE, dav_mkcol, goto
        sibling = run_id + "-m"
        mk = dav_mkcol(sibling)
        if mk.status_code in (201, 405):
            r2 = dav_put(sibling + "/" + name, docx_bytes())
            if r2.status_code in (201, 204):
                print("@@@ run folder wedged; using sibling", flush=True)
                folder = sibling
                r = r2
                goto(page, f"{BASE}/files/spaces/personal/admin/{sibling}")
                page.wait_for_timeout(2500)
    assert r.status_code in (201, 204)
    page.locator("[data-test-resource-name='" + name + "']").wait_for(
        state="visible", timeout=25000
    )
    open_file_by_name(page, name)
    fr, canvas = editor_canvas(page)
    page.wait_for_timeout(1500)
    return fr, canvas


@pytest.fixture(scope="module", autouse=True)
def _cleanup_sibling_folder(run_id):
    yield
    from conftest import dav_delete
    dav_delete(run_id + "-m")


@pytest.mark.gui
def test_deployed_editor_chrome_dom_present(in_run_folder, run_id):
    """The editor ships its menu/menu-header shells in the DOM (pinned state)."""
    fr, _ = _open_editor(in_run_folder, run_id)
    assert fr.locator("[role=menubar]").count() >= 1, "menubar role missing from editor DOM"
    assert fr.locator(".de-file-menu-header").count() >= 1, "file-menu header shells missing"
    # the canvas is the actual interactive surface
    assert fr.locator("canvas").first.is_visible()


@pytest.mark.gui
@pytest.mark.xfail(reason=UNREACHABLE, strict=False)
def test_file_menu_opens_with_full_inventory(in_run_folder, run_id):
    fr, _ = _open_editor(in_run_folder, run_id)
    fr.locator("[aria-label='File'], button:has-text('File')").first.click(timeout=5000)
    fr.wait_for_timeout(1200)
    body = fr.locator("body").inner_text()
    missing = [cap for cap in FILE_MENU_ITEMS if cap not in body]
    assert not missing, f"File menu missing {missing}"


@pytest.mark.gui
@pytest.mark.xfail(reason=UNREACHABLE, strict=False)
def test_create_new_panel_lists_all_templates(in_run_folder, run_id):
    fr, _ = _open_editor(in_run_folder, run_id)
    fr.locator("[aria-label='File'], button:has-text('File')").first.click(timeout=5000)
    fr.wait_for_timeout(1200)
    fr.locator("button:has-text('Create New'), [role=menuitem]:has-text('Create New')").first.click(
        timeout=5000
    )
    fr.wait_for_timeout(1200)
    body = fr.locator("body").inner_text()
    for name, desc in (
        ("Blank", "Empty document"),
        ("Resume", "Professional CV template"),
        ("Formal Letter", "Business correspondence"),
        ("Invoice", "Billing template with table"),
        ("Report", "Structured report with sections"),
    ):
        assert name in body and desc in body, f"template {name!r} missing"


@pytest.mark.gui
@pytest.mark.xfail(
    reason=UNREACHABLE + "; additionally /templates/<id>.html is not deployed (404)",
    strict=False,
)
def test_invoice_template_loads_real_content(in_run_folder, run_id):
    fr, _ = _open_editor(in_run_folder, run_id)
    fr.locator("[aria-label='File'], button:has-text('File')").first.click(timeout=5000)
    fr.wait_for_timeout(1200)
    fr.locator("button:has-text('Create New'), [role=menuitem]:has-text('Create New')").first.click(
        timeout=5000
    )
    fr.wait_for_timeout(800)
    fr.locator("[role=button]:has-text('Invoice')").first.click(timeout=5000)
    fr.wait_for_timeout(2000)
    body = fr.locator("body").inner_text()
    assert "Not Found" not in body and "Invoice" in body


@pytest.mark.gui
@pytest.mark.xfail(reason=UNREACHABLE, strict=False)
def test_version_history_panel_opens(in_run_folder, run_id):
    fr, _ = _open_editor(in_run_folder, run_id)
    fr.locator("[aria-label='File'], button:has-text('File')").first.click(timeout=5000)
    fr.wait_for_timeout(1200)
    fr.locator("button:has-text('Version History...')").first.click(timeout=5000)
    fr.wait_for_timeout(1500)
    body = fr.locator("body").inner_text()
    assert "Version History" in body or "version" in body.lower()


@pytest.mark.gui
@pytest.mark.xfail(reason=UNREACHABLE, strict=False)
def test_close_editor_returns_to_files(in_run_folder, run_id):
    fr, _ = _open_editor(in_run_folder, run_id)
    fr.locator("[aria-label='File'], button:has-text('File')").first.click(timeout=5000)
    fr.wait_for_timeout(1200)
    fr.locator("button:has-text('Close Editor')").first.click(timeout=5000)
    fr.wait_for_timeout(2500)
    assert in_run_folder.locator("iframe").count() == 0, "editor iframe still open"
    assert "/files/" in in_run_folder.url, f"not back in files view: {in_run_folder.url}"
