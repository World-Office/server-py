"""Word-editor interaction tests — the full editing experience via the GUI.

feature register: F-010 F-011 F-012 F-013 F-060 F-061 F-062 (harness-graph)

Covers the originally reported bugs, against HEAD's contenteditable surface
(`#editor` inside the WorldOffice iframe):
  * clicking into the document content must place the caret (click regression)
  * typing must mark the document dirty and trigger a save that succeeds
  * saved content must round-trip to storage (WebDAV check)

The old "browser WASM serialize drops freshly typed chars" xfail is retired:
the current pipeline is HTML -> converter -> save, and typed characters land
in storage (verified end-to-end by the integrity + storage tests here).
"""

import random

import pytest

from conftest import (
    dav_contains,
    dav_delete,
    editor_canvas,
    open_file_by_name,
)


@pytest.mark.gui
def test_click_into_document_content_places_caret(uploaded_docx):
    """REGRESSION: clicking the editing surface must focus the editor."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    fr, editor = editor_canvas(page)
    editor.click()
    page.wait_for_timeout(500)
    assert editor.is_visible(), "editor disappeared after click"
    active = fr.evaluate("document.activeElement ? document.activeElement.id || document.activeElement.tagName : 'none'")
    assert active == "editor" or editor.evaluate("el => el.contains(document.activeElement)"), (
        f"focus was not captured by the editing surface (activeElement={active})"
    )


@pytest.mark.gui
def test_typing_triggers_save(uploaded_docx):
    """Typing must arm the autosave and fire a save request that succeeds."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    _fr, editor = editor_canvas(page)
    editor.click()

    seen = []

    def on_response(r):
        if r.request.method == "POST" and ("/save" in r.url or "/contents" in r.url):
            seen.append(r.status)

    page.on("response", on_response)
    page.keyboard.type("AUTOSAVE-PROOF", delay=30)
    # autosave arms with a 30s debounce after the last input (editor.js
    # markDirty -> setTimeout(saveDocument, 30000)) — poll until it fires
    for _ in range(20):
        if seen:
            break
        page.wait_for_timeout(3000)
    assert seen, "no save request fired after typing"
    assert any(s in (200, 204) for s in seen), f"save request failed: {seen}"


@pytest.mark.gui
def test_enter_and_backspace_shape_paragraphs(uploaded_docx):
    """Structural editing keys must not crash the editor."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    _fr, editor = editor_canvas(page)
    editor.click()
    page.keyboard.press("Enter")
    page.keyboard.type("A", delay=25)
    page.keyboard.press("Backspace")
    page.keyboard.type("B", delay=25)
    page.wait_for_timeout(2000)
    assert editor.is_visible(), "editor disappeared during structural editing"
    assert "B" in editor.inner_text(), "typed content lost during structural editing"


@pytest.mark.gui
def test_typed_text_persists_to_storage(uploaded_docx):
    """Typed characters must reach storage via the save pipeline.

    Ctrl+S → docserver converts html→docx → collaboration PutFile upstream
    → OpenCloud storage; the DAV file then unzips with the token present.
    """
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    _fr, editor = editor_canvas(page)
    editor.click()
    token = f"PERSIST-{random.randint(10000, 99999)}"
    page.keyboard.type(token, delay=30)
    page.keyboard.press("Control+s")
    assert dav_contains(path, token, timeout_s=60), (
        f"typed token never reached storage via the save pipeline ({path})"
    )


@pytest.mark.gui
def test_document_reloads_with_saved_content(uploaded_docx):
    """Reopening the file must render the stored content (no blank editor)."""
    page, name, path = uploaded_docx
    open_file_by_name(page, name)
    _fr, editor = editor_canvas(page)
    page.wait_for_timeout(1500)
    # reopen: navigate back and open again
    page.go_back()
    page.wait_for_timeout(3500)
    open_file_by_name(page, name)
    fr2, editor2 = editor_canvas(page)
    assert editor2.is_visible(), "second open rendered no editing surface"
    body = fr2.locator("#editor").inner_text()
    assert "anchor" in body.lower(), (
        f"reopened document lost its seed content: {body[:120]!r}"
    )
    dav_delete(path)
