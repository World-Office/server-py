"""Multi-session GUI tests: the same document opened from two browser contexts.

True character-level collaboration is a placeholder in the editor (WOPI-only
mode); these tests assert both independent sessions can open and render the
same document concurrently without breaking each other.
"""

import random

import pytest

from conftest import (
    UA,
    dav_delete,
    dav_put,
    docx_bytes,
    editor_canvas,
    goto,
    login,
    open_file_by_name,
    BASE,
)


@pytest.mark.gui
def test_two_sessions_open_same_document(pw, session_ctx, run_id):
    name = f"e2e-multi-{random.randint(1000, 9999)}.docx"
    path = f"{run_id}/{name}"
    r = dav_put(path, docx_bytes())
    assert r.status_code in (201, 204)

    # second, fully independent browser context with its own login
    ctx2 = pw.new_context(
        ignore_https_errors=True, viewport={"width": 1440, "height": 900}, user_agent=UA
    )
    p2 = ctx2.new_page()
    login(p2)
    assert "/files/" in p2.url or "login" not in p2.url.lower(), "second login failed"

    p1 = session_ctx.new_page()
    goto(p1, f"{BASE}/files/spaces/personal/admin/{run_id}")
    p1.wait_for_timeout(2500)
    open_file_by_name(p1, name)
    fr1, editor1 = editor_canvas(p1)

    goto(p2, f"{BASE}/files/spaces/personal/admin/{run_id}")
    p2.wait_for_timeout(2500)
    open_file_by_name(p2, name)
    fr2, editor2 = editor_canvas(p2)

    assert editor1.is_visible() and editor2.is_visible(), (
        "both sessions must render the document concurrently"
    )

    # graceful unload BEFORE closing: an abrupt close with a live editor
    # wedges the folder's reva id-cache for minutes (late async save)
    from conftest import _graceful_editor_unload, close_editor
    _graceful_editor_unload(p1)
    _graceful_editor_unload(p2)
    try:
        close_editor(p1, run_id, file_path=path)
    except Exception as e:
        print(f"multisession teardown: {e}", flush=True)
    ctx2.close()
    dav_delete(path)
