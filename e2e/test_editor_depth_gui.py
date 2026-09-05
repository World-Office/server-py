"""Depth tests for the embedded word editor: keyboard, layout, lifecycle.

The editor shell is deliberately spartan: WASM canvas + a file menu header +
a hidden document-template chooser. Depth is therefore tested through
keyboard interaction, viewport changes, and session lifecycle.
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
)

TEMPLATE_NAMES = ("Blank", "Resume", "Formal Letter", "Invoice", "Report")


def _upload_docx(in_run_folder, run_id) -> tuple[object, str, str]:
    name = f"e2e-depth-{random.randint(1000, 9999)}.docx"
    r = dav_put(f"{run_id}/{name}", docx_bytes())
    assert r.status_code in (201, 204)
    in_run_folder.reload()
    in_run_folder.wait_for_timeout(3000)
    return in_run_folder, name, f"{run_id}/{name}"


@pytest.mark.gui
def test_editor_shell_complete(uploaded_docx):
    """The editor shell renders: canvas, file menu header, template chooser."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr = editor_frame(page)
    assert fr.locator("canvas").first.is_visible(), "canvas missing"
    assert fr.locator("[role=menubar], .de-file-menu-header").count() >= 1, (
        "file menu header missing"
    )
    # document templates ship with the shell (Blank, Resume, Letter, Invoice, Report)
    templates = fr.locator("[role=button]")
    assert templates.count() >= 4, f"template chooser incomplete ({templates.count()} cards)"


@pytest.mark.gui
def test_ctrl_s_fires_wopi_save(uploaded_docx):
    """Explicit Ctrl+S must trigger the WOPI content upload."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr, canvas = editor_canvas(page)
    canvas.click(position={"x": 100, "y": 130})

    seen = []

    def on_response(r):
        if "/contents" in r.url and r.request.method in ("POST", "PUT"):
            seen.append(r.status)

    page.on("response", on_response)
    page.keyboard.type("SAVEKEY", delay=25)
    page.keyboard.press("Control+s")
    page.wait_for_timeout(6000)
    assert seen, "Ctrl+S produced no WOPI content upload"
    assert any(s == 200 for s in seen), f"Ctrl+S save failed: {seen}"


@pytest.mark.gui
def test_undo_redo_keeps_editor_alive(uploaded_docx):
    """Ctrl+Z / Ctrl+Y bursts must not crash the canvas."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr, canvas = editor_canvas(page)
    canvas.click(position={"x": 100, "y": 130})
    page.keyboard.type("UNDO-REDO", delay=20)
    for _ in range(3):
        page.keyboard.press("Control+z")
        page.wait_for_timeout(150)
    for _ in range(2):
        page.keyboard.press("Control+y")
        page.wait_for_timeout(150)
    page.wait_for_timeout(3000)
    assert canvas.is_visible(), "canvas died after undo/redo"


@pytest.mark.gui
def test_viewport_resize_rerenders(uploaded_docx):
    """Narrowing the window must keep the canvas alive (responsive layout)."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr, canvas = editor_canvas(page)
    page.set_viewport_size({"width": 900, "height": 700})
    page.wait_for_timeout(2000)
    assert canvas.is_visible(), "canvas lost after resize (narrow)"
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(2000)
    assert canvas.is_visible(), "canvas lost after resize (restore)"


@pytest.mark.gui
def test_editor_surveys_direct_url_reload(uploaded_docx):
    """Reloading the editor URL must re-open the document (no dead white page)."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr, canvas = editor_canvas(page)
    editor_url = page.url
    assert "external-worldoffice" in editor_url or "editor" in editor_url.lower(), (
        f"unexpected editor URL: {editor_url}"
    )
    page.reload()
    page.wait_for_timeout(10000)
    fr2 = editor_frame(page)
    canvas2 = fr2.locator("canvas").first
    canvas2.wait_for(state="visible", timeout=20000)
    assert canvas2.is_visible(), "editor did not survive a direct URL reload"


@pytest.mark.gui
def test_typing_many_paragraphs_stays_stable(uploaded_docx):
    """A burst of 12 Enter-separated lines must not break the editor."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr, canvas = editor_canvas(page)
    canvas.click(position={"x": 100, "y": 130})
    for i in range(12):
        page.keyboard.type(f"Line {i + 1}", delay=10)
        page.keyboard.press("Enter")
    page.wait_for_timeout(5000)
    assert canvas.is_visible(), "canvas died during paragraph burst"
