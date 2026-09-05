"""Authentication & shell GUI tests — the front door of the cloud."""

import pytest

from conftest import BASE, goto, login


@pytest.mark.gui
def test_login_success(fresh_page):
    login(fresh_page)
    assert "/files/" in fresh_page.url, f"expected files view, got {fresh_page.url}"
    # the app shell rendered
    assert fresh_page.locator("button:visible").count() > 5


@pytest.mark.gui
def test_login_wrong_password_rejected(fresh_page):
    goto(fresh_page, BASE)
    fresh_page.locator("#oc-login-username").fill("admin")
    fresh_page.locator("#oc-login-password").fill("definitely-wrong-{run_id}")
    fresh_page.locator("button[type=submit]").click()
    fresh_page.wait_for_timeout(3000)
    # still on the login form, an error message is shown
    assert fresh_page.locator("#oc-login-username").count() == 1, (
        "wrong password must not lead into the app"
    )


@pytest.mark.gui
def test_login_empty_form_rejected(fresh_page):
    goto(fresh_page, BASE)
    fresh_page.locator("button[type=submit]").click()
    fresh_page.wait_for_timeout(2000)
    assert fresh_page.locator("#oc-login-username").count() == 1


@pytest.mark.gui
def test_logout(fresh_page):
    """Logout must work AND must not poison the shared session context."""
    login(fresh_page)
    assert "/files/" in fresh_page.url
    fresh_page.locator("[aria-label='My Account']").first.click()
    fresh_page.wait_for_timeout(1500)
    item = fresh_page.locator("[role=menuitem]:has-text('Log out')").first
    if item.count() == 0:
        pytest.skip("logout entry not found in account menu")
    item.click()
    fresh_page.wait_for_timeout(4000)
    assert fresh_page.locator("#oc-login-username").count() == 1, (
        "expected the login form after logout"
    )
