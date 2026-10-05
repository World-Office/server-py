"""Depth tests for the embedded word editor: keyboard, layout, lifecycle.

HEAD's editor (python-docserver) is a contenteditable surface inside an
iframe: `#menu-bar` (File menu: New/Open/Export/Print/History/AI) + a
formatting `#toolbar` + the `#editor` div. Depth is tested through keyboard
interaction, viewport changes, and session lifecycle. The WASM canvas era
(documenteditor-react) is retired; these tests pin the deployed surface.
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


@pytest.mark.gui
def test_editor_shell_complete(uploaded_docx):
    """The editor shell renders: menu bar with File menu, toolbar, editor."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr = editor_frame(page)
    assert fr.locator("#menu-bar").is_visible(), "menu bar missing"
    assert fr.locator("#btn-file").is_visible(), "File menu trigger missing"
    assert fr.locator("#toolbar[data-cmd], #toolbar").first.is_visible(), (
        "formatting toolbar missing"
    )
    _fr, editor = editor_canvas(page)
    assert editor.is_visible(), "contenteditable #editor surface missing"

    # the File menu must actually open with its inventory
    fr.locator("#btn-file").click()
    fr.locator("#file-menu").wait_for(state="visible", timeout=5000)
    items = " ".join(fr.locator("#file-menu [role=menuitem]").all_inner_texts())
    for want in ("New", "Open", "Export", "Print", "History", "AI changes"):
        assert want.lower() in items.lower(), f"File menu lacks {want!r}: {items!r}"


@pytest.mark.gui
def test_ctrl_s_fires_save(uploaded_docx):
    """Explicit Ctrl+S must trigger a save request that succeeds."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    _fr, editor = editor_canvas(page)
    editor.click()
    page.keyboard.type("SAVEKEY", delay=25)

    seen = []

    def on_response(r):
        if r.request.method == "POST" and ("/save" in r.url or "/contents" in r.url):
            seen.append(r.status)

    page.on("response", on_response)
    page.keyboard.press("Control+s")
    page.wait_for_timeout(6000)
    assert seen, "Ctrl+S produced no save request"
    assert any(s in (200, 204) for s in seen), f"Ctrl+S save failed: {seen}"


@pytest.mark.gui
def test_undo_redo_keeps_editor_alive(uploaded_docx):
    """Ctrl+Z / Ctrl+Y bursts must not crash the editing surface."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    _fr, editor = editor_canvas(page)
    editor.click()
    page.keyboard.type("UNDO-REDO", delay=20)
    for _ in range(3):
        page.keyboard.press("Control+z")
        page.wait_for_timeout(150)
    for _ in range(2):
        page.keyboard.press("Control+y")
        page.wait_for_timeout(150)
    page.wait_for_timeout(2000)
    assert editor.is_visible(), "editor died after undo/redo"


@pytest.mark.gui
def test_viewport_resize_rerenders(uploaded_docx):
    """Narrowing the window must keep the editing surface alive."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    _fr, editor = editor_canvas(page)
    page.set_viewport_size({"width": 900, "height": 700})
    page.wait_for_timeout(1500)
    assert editor.is_visible(), "editor lost after resize (narrow)"
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(1500)
    assert editor.is_visible(), "editor lost after resize (restore)"


@pytest.mark.gui
def test_editor_surveys_direct_url_reload(uploaded_docx):
    """Reloading the editor URL must re-open the document (no dead white page)."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    editor_url = page.url
    assert "external-worldoffice" in editor_url or "editor" in editor_url.lower(), (
        f"unexpected editor URL: {editor_url}"
    )
    page.reload()
    page.wait_for_timeout(8000)
    fr = editor_frame(page)
    editor2 = fr.locator("#editor").first
    editor2.wait_for(state="visible", timeout=20000)
    assert editor2.is_visible(), "editor did not survive a direct URL reload"


@pytest.mark.gui
def test_typing_many_paragraphs_stays_stable(uploaded_docx):
    """A burst of 12 Enter-separated lines must land in the contenteditable."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr, editor = editor_canvas(page)
    editor.click()
    for i in range(12):
        page.keyboard.type(f"Line {i + 1}", delay=10)
        page.keyboard.press("Enter")
    page.wait_for_timeout(3000)
    assert editor.is_visible(), "editor died during paragraph burst"
    text = fr.locator("#editor").inner_text()
    found = len([ln for ln in text.splitlines() if ln.strip().startswith("Line ")])
    assert found >= 10, f"expected >=10 typed lines in the surface, got {found!r}: {text!r}"
