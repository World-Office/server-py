"""Throwaway smoke test for the full-toolbar feature (editor.js/index.html).

Serves the real docserver on a random port, seeds a DOCX with several
paragraphs, then drives the browser with Playwright to exercise the new
toolbar: alignment buttons, strikethrough, font size/family, text color,
highlight, indent/outdent, line spacing, and the wo-command event bus.
"""
import io
import multiprocessing
import os
import sys
import time
import urllib.error
import urllib.request

# Make the docserver package importable regardless of how the script is
# launched (sys.path[0] would otherwise be scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docx import Document


def _serve(port):
    from src.config import Config
    from src.main import create_app
    import uvicorn

    cfg = Config(
        port=port,
        host="127.0.0.1",
        database=f"/tmp/toolbar-smoke-{port}/docserver.db",
        content_dir=f"/tmp/toolbar-smoke-{port}/documents",
        public_url=f"http://127.0.0.1:{port}",
    )
    import pathlib
    pathlib.Path(cfg.database).parent.mkdir(parents=True, exist_ok=True)
    app = create_app(cfg)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def upload_docx(port):
    doc = Document()
    for line in ["First paragraph alpha", "Second paragraph beta", "Third paragraph gamma"]:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    bytes_ = buf.getvalue()

    boundary = "----smokeboundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="smoke.docx"\r\n'
        'Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n'
    ).encode() + bytes_ + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, resp.read().decode()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    proc = multiprocessing.Process(target=_serve, args=(port,), daemon=True)
    proc.start()

    # Wait for the server, then seed the document.
    for _ in range(60):
        try:
            status, payload = upload_docx(port)
            print("SEED-UPLOAD", status, payload, flush=True)
            break
        except urllib.error.URLError:
            time.sleep(0.25)
    else:
        print("SERVER NEVER CAME UP", flush=True)
        proc.terminate()
        return 1

    from playwright.sync_api import sync_playwright

    failures = []
    def check(name, cond):
        print(("PASS" if cond else "FAIL"), name, flush=True)
        if not cond:
            failures.append(name)

    def wait_html_contains(substr, timeout=4000):
        """Poll until editor innerHTML contains a substring (defeats races)."""
        page.wait_for_function(
            "(s) => document.getElementById('editor').innerHTML.includes(s)",
            arg=substr, timeout=timeout,
        )

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/editor/smoke.docx")
        page.wait_for_selector("#editor p")

        editor = page.locator("#editor")

        # Type a new paragraph at the end.
        editor.click()
        page.keyboard.press("Control+End")
        page.keyboard.type("Toolbar smoke A")
        page.wait_for_timeout(100)
        check("typed-text", "Toolbar smoke A" in editor.inner_text())

        # --- alignment ---
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.click('button[data-cmd="justifyCenter"]')
        page.wait_for_timeout(150)
        center_html = editor.evaluate("(el) => el.innerHTML")
        check("justifyCenter-applies", "text-align" in center_html and "center" in center_html.lower())
        check("justifyCenter-marked-active", page.eval_on_selector_all('button[data-cmd="justifyCenter"].active', "els => els.length") == 1)

        page.click('button[data-cmd="justifyRight"]')
        page.wait_for_timeout(150)
        check("justifyRight-applies", "text-align: right" in editor.evaluate("(el) => el.innerHTML").lower())

        page.click('button[data-cmd="justifyLeft"]')
        page.wait_for_timeout(100)
        page.click('button[data-cmd="justifyFull"]')
        page.wait_for_timeout(150)
        full_html = editor.evaluate("(el) => el.innerHTML")
        check("justifyFull-applies", "justify" in full_html.lower())

        # --- strikethrough on selection ---
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.keyboard.press("Shift+Home")
        page.click('button[data-cmd="strikeThrough"]')
        page.wait_for_timeout(150)
        st_html = editor.evaluate("(el) => el.innerHTML")
        check("strikethrough-applies", "<s>" in st_html or "strike" in st_html or "line-through" in st_html)
        page.click('button[data-cmd="strikeThrough"]')  # toggle off
        page.wait_for_timeout(100)

        # --- font size via select (needs an active text selection) ---
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.keyboard.press("Shift+Home")
        page.select_option("#font-size", "5")
        page.wait_for_timeout(150)
        fs_html = editor.evaluate("(el) => el.innerHTML")
        check("font-size-span-style", "font-size" in fs_html)
        check("no-font-tag", "<font" not in fs_html)

        # --- font family ---
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.keyboard.press("Shift+Home")
        page.select_option("#font-family", "Georgia")
        wait_html_contains("Georgia")
        ff_html = editor.evaluate("(el) => el.innerHTML")
        check("font-family-span-style", "font-family" in ff_html and "Georgia" in ff_html.lower())

        # --- text color ---
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.keyboard.press("Shift+Home")
        page.locator("#text-color").evaluate("(el) => { el.value = '#cc0000'; el.dispatchEvent(new Event('change')); }")
        page.wait_for_timeout(150)
        tc_html = editor.evaluate("(el) => el.innerHTML")
        check("text-color-span-style", "color" in tc_html)

        # --- highlight ---
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.keyboard.press("Shift+Home")
        page.locator("#highlight-color").evaluate("(el) => { el.value = '#ffff00'; el.dispatchEvent(new Event('change')); }")
        page.wait_for_timeout(150)
        hl_html = editor.evaluate("(el) => el.innerHTML")
        check("highlight-span-style", "background-color" in hl_html)

        # --- indent/outdent ---
        before = editor.evaluate("(el) => el.innerHTML")
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.click('button[data-cmd="indent"]')
        page.wait_for_timeout(150)
        after_indent = editor.evaluate("(el) => el.innerHTML")
        check("indent-changes-dom", after_indent != before)
        page.click('button[data-cmd="outdent"]')
        page.wait_for_timeout(100)

        # --- line spacing ---
        page.click('#editor p:has-text("Toolbar smoke A")')
        page.select_option("#line-spacing", "1.5")
        page.wait_for_timeout(150)
        ls_html = editor.evaluate("(el) => el.innerHTML")
        check("line-spacing-applies", "line-height" in ls_html and "1.5" in ls_html)

        # --- wo-command bus: dispatch a justify through the event, not the button ---
        page.evaluate("() => window.dispatchEvent(new CustomEvent('wo-command', {detail:{command:'justifyCenter', value:null}}))")
        page.wait_for_timeout(150)
        bus_html = editor.evaluate("(el) => el.innerHTML")
        check("wo-command-bus-drives-formatting", "center" in bus_html.lower())

        # --- undo/redo works after new commands (snapshot chain) ---
        undo_btn = page.locator('button[data-cmd="undo"]')
        redo_btn = page.locator('button[data-cmd="redo"]')
        check("undo-enabled-after-formatting", undo_btn.is_enabled())
        undo_btn.click()
        page.wait_for_timeout(150)
        reverted = editor.evaluate("(el) => el.innerHTML")
        check("undo-reverts-formatting", "background-color" not in reverted)
        check("redo-enabled-after-undo", redo_btn.is_enabled())
        redo_btn.click()
        page.wait_for_timeout(150)

        # --- keyboard shortcut: Ctrl+E centers ---
        page.click('#editor p:has-text("First paragraph alpha")')
        page.keyboard.press("Control+e")
        page.wait_for_timeout(150)
        kb_html = editor.evaluate("(el) => el.innerHTML")
        check("ctrl-e-centers", "text-align" in kb_html and "center" in kb_html.lower())

        # --- save round-trips without error ---
        with page.expect_response(lambda r: r.url.endswith("/save") and r.request.method == "POST") as resp_info:
            page.click("#btn-save")
        resp = resp_info.value
        check("save-ok", resp.status in (200, 201))

        browser.close()

    print("\n=== SUMMARY ===", "ALL-PASS" if not failures else f"FAILURES: {failures}", flush=True)
    proc.terminate()
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
