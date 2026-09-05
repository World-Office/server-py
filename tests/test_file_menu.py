"""TDD test for File-Menu UI (editor-cloud-ui T6).

feature register: F-005 F-078 F-079 (menu surface wiring)

RED: fails until index.html has a file menu (New/Open/Export/Print) and
editor.js wires the commands. This is an executable smoke test because no
JS test runner (Playwright/jsdom) is available in this environment.
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = (WEB / "editor.js").read_text(encoding="utf-8")
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")


def test_file_menu_present_in_html():
    for el in ["btn-new", "btn-open", "btn-export", "btn-print"]:
        assert f'id="{el}"' in HTML, f"missing #{el} in index.html"


def test_export_submenu_formats_present():
    for fmt in ["pdf", "odt", "html", "docx"]:
        assert f'data-export="{fmt}"' in HTML, f"missing export format {fmt}"


def test_editor_js_wires_file_commands():
    assert "doNewDocument" in JS, "editor.js missing doNewDocument"
    assert "doExport" in JS, "editor.js missing doExport"
    assert "doPrint" in JS, "editor.js missing doPrint"
    # each command must be hooked to a DOM element
    assert "btn-new" in JS, "editor.js does not reference btn-new"
    assert "btn-export" in JS, "editor.js does not reference btn-export"
    assert "btn-print" in JS, "editor.js does not reference btn-print"


def test_inline_text_format_buttons_present():
    """code / small-caps / all-caps toolbar commands exist (T6 UI)."""
    for cmd in ["strikeThrough", "smallCaps", "allCaps", "code",
                "superscript", "subscript"]:
        assert f'data-cmd="{cmd}"' in HTML, f"missing button data-cmd={cmd} in index.html"
    for fn in ["toggleInlineCSS", "toggleMonospace", "fontIsMono", "spanStyleActive"]:
        assert fn in JS, f"editor.js missing {fn}"


def test_paragraph_format_commands_present():
    """RTL button + block-level paragraph commands exist (T8 UI)."""
    assert 'data-cmd="directionRtl"' in HTML
    assert "toggleBlockDirection" in JS
    assert "applyLineHeight" in JS


def test_insert_date_button_wired():
    """Date/time insert button + command exist (insert parity T27)."""
    assert 'id="btn-datetime"' in HTML
    assert "insertDate" in JS
    assert 'id="btn-hr"' in HTML


def test_image_resize_fields_wired():
    """Image dialog exposes width/height resize inputs wired into confirm."""
    assert 'id="image-width"' in HTML and 'id="image-height"' in HTML
    assert 'id="image-size-fields"' in HTML
    assert "dims.push(" in JS  # confirmImageDialog attaches width attr
    assert "Image.Width" in I18N and "Image.Height" in I18N


def test_version_history_wired():
    """Version history: menu entry, dialog, list+restore logic, i18n (T30)."""
    assert 'id="btn-history"' in HTML, "File menu missing History entry"
    assert 'id="version-history-dialog"' in HTML, "missing version-history dialog"
    assert 'id="version-list"' in HTML and 'id="version-error"' in HTML
    for fn in ["openVersionHistory", "closeVersionHistory", "restoreVersion",
               "renderVersionList", "formatVersionDate"]:
        assert fn in JS, f"editor.js missing {fn}"
    assert 'api("versions")' in JS, "version list must call the versions endpoint"
    assert 'api("versions/"' in JS, "restore must call the restore endpoint"
    for key in ["VersionHistory.Title", "VersionHistory.Restore",
                "VersionHistory.Current", "VersionHistory.Empty"]:
        assert key in JS, f"editor.js missing i18n key {key}"


def test_insert_menu_buttons_present_and_wired():
    """Insert-surface buttons exist and emit their commands (F-078/F-079)."""
    for el in ["btn-symbol", "btn-datetime"]:
        assert f'id="{el}"' in HTML, f"missing #{el} in index.html"
        assert el in JS, f"editor.js does not reference {el}"
    assert 'cmd === "insertSymbol"' in JS or "insertSymbol" in JS
    assert "insertDate" in JS
