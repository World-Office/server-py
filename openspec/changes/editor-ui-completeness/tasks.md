## 1. Hyperlink (editor-ui/links)

- [x] 1.1 Converter round-trip test: `<a href="https://x">text</a>` survives `html_to_docx`→`docx_to_html` (and a `javascript:` href is dropped).
- [x] 1.2 `converter.py` emits/parses `<a>` (DOCX `w:hyperlink` + external relationship).
- [x] 1.3 `sanitize.py` already allows `<a>` with safe `href` (blocks `javascript:`/`data:` via `_is_safe_link_url`).
- [x] 1.4 "Insert link" toolbar button + dialog (`web/index.html`), `createLink`/`insertHTML` in `web/editor.js`.
- [x] 1.5 Playwright check: insert link, assert it shows and round-trips to the host on save.
      (`tests/e2e/test_cloud_editor_e2e.py::test_insert_link_roundtrip`)

## 2. Text colour & highlight (editor-ui/format-advanced)

- [x] 2.1 Converter round-trip tests: colour + highlight spans and `<sup>`/`<sub>` survive `html_to_docx`→`docx_to_html`.
- [x] 2.2 `converter.py` emits/parses `color`/`background-color` (`w:color`, `w:shd` fill) + `vertAlign`; `_InlineRunBuilder` handles `<span style>`/`<sup>`/`<sub>` with a span/vert stack.
- [x] 2.3 `sanitize.py` keeps `color`/`background-color` (whitelist) and drops `expression(`/`url(`; `sup`/`sub`/`s` added to safe tags.
- [x] 2.4 Toolbar already had colour + highlight `<input type=color>` pickers (wired via `foreColor`/`hiliteColor` + `styleWithCSS`); added superscript/subscript buttons (`data-cmd`) + dirty condition in `emitCommand`.
- [x] 2.5 Playwright check: apply colour + superscript, assert visible and persisted in the host DOCX (`test_format_color_highlight_superscript`).

## 3. Table cell operations (editor-ui/table-cells)

- [x] 3.1 Converter round-trip test: merged cell (`rowspan`) + removed column survives `html_to_docx`→`docx_to_html`.
- [x] 3.2 `editor.js` handlers: insert/delete row and column, merge (selection bounding rect), split (colspan/rowspan reset) — collect cells then remove to avoid live `row.cells` index shifts.
- [x] 3.3 "Table actions" dialog (`#table-ops-dialog`) + `▦✎` toolbar button in `web/index.html`; wired through `finalizeTableChange()` (dirty/history/collab/host).
- [x] 3.4 Playwright check: merge two cells (colspan=2) + delete a column, assert colspan persists to the host DOCX (`test_table_merge_and_column_ops`).

### Bug fixed while implementing 3.x (real data-loss)
`applyRemoteText` re-rendered via `editor.innerText = text` on ANY plain-text mismatch; since the collab layer is a plain-text CRDT, tables/images/links/formatting contribute only whitespace to the projection, so the 400ms poll erased structural content. Fix: whitespace-normalized equality guard — genuine character edits still converge, structure is never destroyed. E2E tests were also isolated per-document (fresh mock-host seed per test) to remove cross-test CRDT contamination.

## 4. Insert primitives (editor-ui/insert-misc)

- [x] 4.1 Converter tests: `<hr>` + page break round-trip `html_to_docx`→`docx_to_html` (structural: `w:pBdr` bottom border; run `<w:br w:type='page'/>`); sanitizer keeps `<hr>` + `div.page-break` and still drops `<script>`.
- [x] 4.2 Converter: `<hr>` ↔ bottom-border paragraph; page-break div ↔ `<w:br w:type='page'/>` paragraph; content left inside the page-break div (Chromium caret-in-marker quirk) is preserved as a paragraph after the break.
- [x] 4.3 UI: `#btn-hr` / `#btn-page-break` toolbar buttons + `#symbol-dialog` picker (46 symbols/emoji, `insertText`). Page-break insert appends a trailing `<p><br></p>` target block (Chromium otherwise appends typed text INTO the marker div at a block boundary); `moveCaretPastStructuralMarkers()` guards symbol inserts at `hr`/`.page-break` carets.
- [x] 4.4 Playwright `test_insert_hr_pagebreak_symbol`: insert hr + page break + § via dialog, assert DOM, save → host bytes contain `<hr`/`page-break`/`§`, reload → all restore from host.

## 5. View controls (editor-ui/view-controls)

- [x] 5.1 Zoom in/out/reset control that scales the editing surface only (CSS `zoom` on `#editor`, persisted in localStorage) in `editor.js` + `index.html`.
- [x] 5.2 Dark/light theme toggle via CSS variables (`html.light` overrides `--bg/--text/...`), persisted in localStorage.
- [x] 5.3 Fullscreen control expanding the editor viewport (`.fullscreen` class + best-effort Fullscreen API).
- [x] 5.4 Playwright check: zoom in grows surface; theme toggle flips background; fullscreen toggles class.
      (`tests/e2e/test_cloud_editor_e2e.py::test_view_controls_zoom_theme_fullscreen`)

## 6. File operations (editor-ui/file-ops)

- [x] 6.1 "New" in `editor.js`: confirm-before-discard, then clear + immediate save (autosave would lag ~30s) so the blank document persists; server-side fresh store entry already available at `POST /api/documents/new` (returns an editor URL for a blank docx/odt).
- [x] 6.2 `GET/POST /api/documents/{id}/export?format=…` for `odt`/`html`/`docx`/`pdf`; File > Export submenu drives `doExport(format)` download in `editor.js`. New unit tests: `tests/test_file_ops.py` (valid ODT zip with `mimetype` + text in `content.xml`; sanitized HTML export; new-document route).
- [x] 6.3 Print control: `doPrint()` → `window.print()`; added `@media print` paper-only stylesheet (chrome hidden, `@page` margins) in `web/style.css`.
- [x] 6.4 Playwright `test_file_menu_export_odt_and_new_document`: File > Export > ODT yields a downloadable ODT whose `content.xml` holds the body text; New confirms, clears the editor and the blank state persists to the host on save.

## 7. Collaboration presence (editor-ui/collab-presence)

- [x] 7.1 Render remote carets from the presence channel; colour per `client_id`; remove on leave (overlay in `editor.js`). Presence is refreshed via the `/collab/state` poll so peer join/leave is reflected without SSE.
- [x] 7.2 Avatar/label chip per peer with stable colour (`peerColor` hash); local identity marked "(you)".
- [x] 7.3 Playwright check: two editors join, each sees the other's coloured caret + chip (covered in `test_two_users_collaborate_save_and_notify_host`).

## 8. Status & indicators (editor-ui/status-bar)

- [x] 8.1 Status bar in `web/index.html` + live word/char count in `editor.js` (GREEN)
- [x] 8.2 Save-status indicator (saved/saving/dirty) reflecting the actual WOPI PUT
      (`#status` + `markDirty`/`saveDocument`; already wired)
- [x] 8.3 Offline queue: save failures that are genuine network errors (`fetch` TypeError) queue the latest snapshot in localStorage (`wo-offline-queue`), show the `#offline-indicator`, restore the snapshot after a reload (so offline edits survive), and flush on the `online` event; a successful save clears the queue. Server-side HTTP errors are NOT queued (they would just fail again). Playwright `test_offline_queue_and_resync`.
- [x] 8.4 Playwright check: type → count updates; edit after save → "unsaved" then "saved"/"ready"
      (`tests/e2e/test_cloud_editor_e2e.py::test_status_bar_word_count_and_save_indicator`)

## 9. Cross-cutting

- [x] 9.1 `.symbol-grid`/print/offline CSS tidy; ruff (`uv run ruff check src tests`) clean; converter round-trip tests added with every group (links, colour/sup-sub, table, hr/page-break) and the full suite stays green (380 passed).
- [x] 9.2 `docs/testing/test-scenarios.md` extended with US-61…US-68 (link, colour/sup-sub, table ops, insert misc, presence, view controls, file export/new, offline queue) in the existing German user-story style with automated acceptance commands.
- [x] 9.3 Full local suite + repeated Playwright e2e passes run before marking each group done (a real `applyRemoteText` data-loss bug + a Chromium caret-in-marker bug + an index-shift bug + cross-test CRDT contamination were found and fixed; every commit green).
