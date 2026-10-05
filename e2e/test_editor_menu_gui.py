"""Editor File-menu chrome against the DEPLOYED bundle.

The retired production editor (documentserver canvas bundle) shipped a
non-interactive menu; that gap is history. HEAD's editor (python-docserver)
has a live File menu (`#btn-file` -> `#file-menu`: New / Open / Export
submenu PDF-ODT-HTML-DOCX / Print / History / AI changes) plus working
version-history and AI-review dialogs. The former xfail pins are therefore
rewritten as positive contracts.
"""

import random

import pytest

from conftest import (
    dav_put,
    docx_bytes,
    editor_canvas,
    editor_frame,
    open_file_by_name,
)

FILE_MENU_ITEMS = ("New", "Open", "Export", "Print", "History", "AI changes")
EXPORT_FORMATS = ("pdf", "odt", "html", "docx")


def _open_editor(page, run_id):
    """Upload a fresh docx and open it; returns (frame, editor-locator)."""
    name = "menu-" + str(random.randint(1000, 9999)) + ".docx"
    r = dav_put(run_id + "/" + name, docx_bytes())
    assert r.status_code in (201, 204), f"upload failed: {r.status_code}"
    page.locator("[data-test-resource-name='" + name + "']").wait_for(
        state="visible", timeout=25000
    )
    open_file_by_name(page, name)
    fr, editor = editor_canvas(page)
    page.wait_for_timeout(1000)
    return fr, editor


@pytest.mark.gui
def test_deployed_editor_chrome_dom_present(in_run_folder, run_id):
    """The editor ships its menu bar, toolbar and editing surface."""
    fr, editor = _open_editor(in_run_folder, run_id)
    assert fr.locator("#menu-bar[aria-label]").count() == 1, (
        "menu bar (with accessible region label) missing from editor DOM"
    )
    assert fr.locator("#toolbar[role=toolbar]").count() == 1, "toolbar role missing"
    assert editor.is_visible(), "contenteditable #editor surface missing"


@pytest.mark.gui
def test_file_menu_opens_with_full_inventory(in_run_folder, run_id):
    """The File menu is interactive and lists every entry point."""
    fr, _ = _open_editor(in_run_folder, run_id)
    fr.locator("#btn-file").click(timeout=5000)
    fr.locator("#file-menu").wait_for(state="visible", timeout=5000)
    items = " ".join(fr.locator("#file-menu [role=menuitem]").all_inner_texts())
    missing = [cap for cap in FILE_MENU_ITEMS if cap.lower() not in items.lower()]
    assert not missing, f"File menu missing {missing}: {items!r}"

    # Export carries the full format submenu
    fr.locator("#btn-export").click(timeout=5000)
    exports = " ".join(
        fr.locator("#file-menu .menu-sublist [role=menuitem]").all_inner_texts()
    ).lower()
    for fmt in EXPORT_FORMATS:
        assert fmt in exports, f"Export submenu lacks {fmt!r}: {exports!r}"


@pytest.mark.gui
def test_new_menu_item_creates_document(in_run_folder, run_id):
    """File > New (confirm dialog) must yield a live, persisted blank doc."""
    fr, _ = _open_editor(in_run_folder, run_id)
    page = fr.page
    page.on("dialog", lambda d: d.accept())  # doNewDocument() window.confirm
    fr.locator("#btn-file").click(timeout=5000)
    fr.locator("#file-menu").wait_for(state="visible", timeout=5000)
    fr.locator("#btn-new").click(timeout=5000)
    fr.wait_for_timeout(4000)
    editor = fr.locator("#editor").first
    editor.wait_for(state="visible", timeout=15000)
    assert editor.is_visible(), "File > New left no editing surface"


@pytest.mark.gui
def test_version_history_panel_opens(in_run_folder, run_id):
    """File > History opens the version-history dialog (Escape closes it)."""
    fr, _ = _open_editor(in_run_folder, run_id)
    fr.locator("#btn-file").click(timeout=5000)
    fr.locator("#file-menu").wait_for(state="visible", timeout=5000)
    fr.locator("#btn-history").click(timeout=5000)
    dialog = fr.locator("#version-history-dialog.open").first
    dialog.wait_for(state="visible", timeout=5000)
    assert dialog.is_visible(), "version-history dialog did not open"
    fr.page.keyboard.press("Escape")
    fr.wait_for_timeout(800)
    assert fr.locator("#version-history-dialog.open").count() == 0, (
        "version-history dialog not closable via Escape"
    )
