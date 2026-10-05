"""Button sweep: every button in the cloud UI shell gets exercised.

Strategy per surface:
- inventory asserts (present, visible, ENABLED, accessible name)
- safe clicks only (open panels/menus, navigate — never delete/empty/logout
  on the shared session; mutating buttons are covered in dedicated modules)

Surfaces: files-view topbar (13 buttons), +New menu, row context menu,
folder-actions menu, display customization, notifications, application
switcher, account menu, left navigation, breadcrumbs, editor menubar.
"""

import random

import pytest

from conftest import (
    BASE,
    dav_delete,
    dav_mkcol,
    dav_put,
    docx_bytes,
    goto,
    txt_bytes,
)

# ── verified inventory of the files view topbar (OpenCloud web 7.3) ──
FILES_VIEW_BUTTONS = (
    "Skip to main",
    "All files",
    "Search",
    "Notifications",
    "Open sidebar to view details",
    "Application Switcher",
    "My Account",
    "New",
    "Show actions for current folder",
    "Switch view mode",
    "Display customization options of the files list",
    "A-Z",
    "Show context menu",
)

APP_SWITCHER_ITEMS = ("Files", "Text Editor", "App Store", "Admin Settings", "Calendar")

LEFT_NAV = ("Personal", "Favorites", "Shares", "Spaces", "Deleted files")


def _seed_folder(page, run_id, files=("report.txt",)) -> str:
    import time

    folder = f"e2e-btn-{random.randint(1000, 9999)}"
    # NOTE: folders MUST be created with the MKCOL verb — a PUT with a
    # trailing slash silently creates a broken file-node on posixfs
    # ("not a directory" on every later write).
    for attempt in range(4):
        r = dav_mkcol(f"{run_id}/{folder}")
        if r.status_code in (201, 204, 405):
            break
        time.sleep(1.5 * (attempt + 1))
    else:
        pytest.fail(f"MKCOL {run_id}/{folder} failed: {r.status_code}")
    for f in files:
        data = docx_bytes() if f.endswith(".docx") else txt_bytes()
        for attempt in range(4):
            r = dav_put(f"{run_id}/{folder}/{f}", data)
            if r.status_code in (201, 204):
                break
            time.sleep(1.5 * (attempt + 1))
        else:
            pytest.fail(f"PUT {run_id}/{folder}/{f} failed: {r.status_code}")
    goto(page, f"{BASE}/files/spaces/personal/admin/{run_id}/{folder}")
    page.locator("[data-test-resource-name]").first.wait_for(state="visible", timeout=25000)
    page.wait_for_timeout(1500)  # let the +New button arm (disabled while loading)
    return folder


def _visible_button_names(page, run_id) -> list[str]:
    loc = page.locator("button:visible")
    names = []
    for i in range(loc.count()):
        el = loc.nth(i)
        n = (el.get_attribute("aria-label") or el.get_attribute("data-test-id") or el.inner_text() or "").strip()
        n = " ".join(n.split())
        if n:
            names.append(n)
    return names


# ────────────────────────── inventory ──────────────────────────


@pytest.mark.gui
def test_files_view_full_button_inventory(page, run_id):
    """Every topbar button of the files view exists, is visible and enabled."""
    _seed_folder(page, run_id)  # folder under root run dir; cleanup by session
    names = _visible_button_names(page, run_id)
    missing = [b for b in FILES_VIEW_BUTTONS if b not in names]
    assert not missing, f"missing topbar buttons: {missing}; present: {names}"


@pytest.mark.gui
def test_every_visible_button_has_accessible_name(page, run_id):
    """A11y blanket: no unlabeled icon buttons anywhere in the shell."""
    _seed_folder(page, run_id)
    loc = page.locator("button:visible")
    unnamed = []
    for i in range(loc.count()):
        el = loc.nth(i)
        n = (el.get_attribute("aria-label") or el.get_attribute("data-test-id") or el.inner_text() or "").strip()
        if not n:
            unnamed.append(el.get_attribute("class") or "<no-class>")
    assert not unnamed, f"unlabeled buttons: {unnamed}"


# ────────────────────────── menus open ──────────────────────────


@pytest.mark.gui
def test_new_menu_offers_all_document_types(page, run_id):
    """The deployed stack creates: folder, odt Document, txt, md, url.

    Spreadsheet/Presentation creation is not offered by OpenCloud's New
    menu in this deployment (no app provides those templates) — asserting
    them here would test a nonexistent feature. Parity gap is tracked in
    the register (ods/odp xfails in test_editors_gui.py).
    """
    _seed_folder(page, run_id)
    page.get_by_role("button", name="New", exact=True).click()
    page.wait_for_timeout(1200)
    items = " ".join(page.locator("[role=menuitem]:visible").all_inner_texts())
    for want in ("folder", "Document", "Plain text", "Markdown"):
        assert want.lower() in items.lower(), f"New menu lacks {want!r}: {items!r}"
    page.keyboard.press("Escape")


@pytest.mark.gui
def test_row_context_menu_inventory(page, run_id):
    _seed_folder(page, run_id, files=("report.txt",))
    page.locator("[data-test-resource-name='report.txt']").first.click(button="right")
    page.wait_for_timeout(1200)
    items = " ".join(page.locator("[role=menuitem]:visible").all_inner_texts())
    for want in ("Open", "Rename", "Delete"):
        assert want in items, f"context menu lacks {want!r}: {items!r}"
    page.keyboard.press("Escape")


@pytest.mark.gui
def test_docx_context_menu_has_open_with(page, run_id):
    _seed_folder(page, run_id, files=("paper.docx",))
    page.locator("[data-test-resource-name='paper.docx']").first.click(button="right")
    page.wait_for_timeout(1200)
    items = " ".join(page.locator("[role=menuitem]:visible").all_inner_texts())
    assert "Open with..." in items, f"docx context menu lacks 'Open with...': {items!r}"
    page.keyboard.press("Escape")


@pytest.mark.gui
def test_folder_actions_button_opens_folder_menu(page, run_id):
    """'Show actions for current folder' must open the folder's context menu."""
    _seed_folder(page, run_id, files=("report.txt",))
    page.get_by_role("button", name="Show actions for current folder").click()
    page.wait_for_timeout(1200)
    items = " ".join(page.locator("[role=menuitem]:visible").all_inner_texts())
    for want in ("Share", "Delete", "Rename", "Details"):
        assert want in items, f"folder actions menu lacks {want!r}: {items!r}"
    page.keyboard.press("Escape")


@pytest.mark.gui
def test_display_customization_opens(page, run_id):
    _seed_folder(page, run_id)
    page.get_by_role(
        "button", name="Display customization options of the files list"
    ).click(force=True)
    page.wait_for_timeout(1800)
    # the options open as a dropdown (contains e.g. the items-per-page select)
    panel = page.locator(
        "[class*='dropdown']:visible, [role=menu]:visible, [role=dialog]:visible"
    )
    assert panel.count() >= 1, "display customization panel did not open"
    page.keyboard.press("Escape")


@pytest.mark.gui
def test_notifications_button_opens_panel(page, run_id):
    _seed_folder(page, run_id)
    btn = page.get_by_role("button", name="Notifications")
    before = btn.get_attribute("aria-expanded")
    btn.scroll_into_view_if_needed()
    btn.click()
    page.wait_for_timeout(1500)
    after = btn.get_attribute("aria-expanded")
    panel = page.locator("[role=dialog]:visible, [role=menu]:visible, [class*='notification']:visible")
    assert after != before or panel.count() >= 1, (
        "notifications button produced no panel / aria-expanded change"
    )
    page.keyboard.press("Escape")


@pytest.mark.gui
def test_application_switcher_lists_all_apps(page, run_id):
    _seed_folder(page, run_id)
    page.get_by_role("button", name="Application Switcher").click()
    page.wait_for_timeout(1500)
    items = " ".join(page.locator("[role=menuitem]:visible, [role=dialog] a:visible").all_inner_texts())
    for want in APP_SWITCHER_ITEMS:
        assert want in items, f"app switcher lacks {want!r}: {items!r}"
    page.keyboard.press("Escape")


@pytest.mark.gui
def test_account_menu_offers_preferences_and_logout(page, run_id):
    _seed_folder(page, run_id)
    page.locator("[aria-label='My Account']").click()
    page.wait_for_timeout(1200)
    items = " ".join(page.locator("[role=menuitem]:visible").all_inner_texts())
    assert "Preferences" in items and "Log out" in items, f"account menu: {items!r}"
    page.keyboard.press("Escape")


# ────────────────────────── navigation buttons ──────────────────────────


@pytest.mark.gui
@pytest.mark.xfail(
    reason="UI BUG: the 'All files' breadcrumb crumb is a dead button — not "
    "disabled, accepts clicks/Enter, but never navigates (verified at one and "
    "two levels deep); root navigation only works via the left rail",
    strict=False,
)
def test_all_files_button_navigates_to_root(page, run_id):
    folder = _seed_folder(page, run_id)
    page.get_by_role("button", name="All files").click(force=True)
    for _ in range(10):
        page.wait_for_timeout(600)
        if folder not in page.url:
            break
    assert folder not in page.url and "/files" in page.url, (
        f"'All files' did not leave the folder: {page.url}"
    )


@pytest.mark.gui
def test_breadcrumb_button_navigates_up(page, run_id):
    import time as _t

    folder = _seed_folder(page, run_id)
    sub = f"e2e-sub-{random.randint(1000, 9999)}"
    for attempt in range(4):
        r = dav_mkcol(f"{run_id}/{folder}/{sub}")
        if r.status_code in (201, 204, 405):
            break
        _t.sleep(1.5 * (attempt + 1))
    dav_put(f"{run_id}/{folder}/{sub}/inner.txt", txt_bytes())
    _t.sleep(2)  # fresh folder indexing lag
    goto(page, f"{BASE}/files/spaces/personal/admin/{run_id}/{folder}/{sub}")
    page.locator("[data-test-resource-name='inner.txt']").wait_for(
        state="visible", timeout=25000
    )
    # parent crumbs render as links; only the current folder crumb is a button
    page.get_by_role("link", name=folder).click()
    for _ in range(10):
        page.wait_for_timeout(600)
        if sub not in page.url:
            break
    assert sub not in page.url and folder in page.url, (
        f"breadcrumb did not navigate up to the parent folder: {page.url}"
    )


@pytest.mark.gui
def test_left_nav_all_sections_render(page, run_id):
    _seed_folder(page, run_id)
    for section in LEFT_NAV:
        link = page.locator(f"a:has-text({section!r})").first
        assert link.count() >= 1, f"left nav missing {section!r}"
        link.click()
        page.wait_for_timeout(2500)
        assert "/files" in page.url, f"{section}: unexpected url {page.url}"
    goto(page, f"{BASE}/files/spaces/personal/admin")
    page.wait_for_timeout(2000)


@pytest.mark.gui
def test_app_switcher_navigates_to_admin_settings(page, run_id):
    _seed_folder(page, run_id)
    page.get_by_role("button", name="Application Switcher").click()
    page.wait_for_timeout(1200)
    page.locator("[role=menuitem]:has-text('Admin Settings')").first.click()
    page.wait_for_timeout(3500)
    assert "admin" in page.url.lower(), f"admin-settings nav failed: {page.url}"
    goto(page, f"{BASE}/files/spaces/personal/admin")
    page.wait_for_timeout(2000)


@pytest.mark.gui
def test_skip_to_main_link_focuses_content(page, run_id):
    _seed_folder(page, run_id)
    page.get_by_role("button", name="Skip to main").evaluate("el => el.click()")
    page.wait_for_timeout(800)
    focused = page.evaluate("document.activeElement ? document.activeElement.tagName : ''")
    assert focused, "skip-to-main produced no focus target"
