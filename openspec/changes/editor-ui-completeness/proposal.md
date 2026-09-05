## Why

The editor already covers the core toolbar (bold/italic/underline/strike,
headings H1–H3, alignment, lists, indent, table/image insert, find-replace,
undo/redo, save) and is verified end-to-end against OpenCloud (WOPI lock live,
30/30 opencloud E2E tests, 313 local tests green). But compared with a usable
office editor at Euro-Office parity it is still missing a set of standard
UI elements that users expect: links, text colour/highlight, table-cell
operations, common insert primitives, view controls, file operations, live
collaboration presence, and a status/indicator bar. These are the "remaining
missing UI elements" needed before the editor can be called feature-complete.

## What Changes

Adds the missing editor UI elements, each backed by converter + sanitizer
round-trip support so the feature survives save/load (the hard part we already
learned from the image/table work):

- **Hyperlink insertion** — link dialog + `<a href>` in editor, converter and sanitizer.
- **Text colour & highlight** — `foreColor`/`backColor` with safe-colour list; survive round-trip.
- **Table cell operations** — merge/split cells, insert/delete row/column, from the existing table UI.
- **Insert primitives** — horizontal rule, page break, symbol/emoji picker.
- **View controls** — zoom in/out, dark-mode toggle, fullscreen.
- **File operations** — new blank document, export to PDF/ODT/HTML, print.
- **Collaboration presence UI** — remote cursors + avatars on top of the existing CRDT hub.
- **Status & indicators** — word count, save-status indicator, offline indicator.

## Capabilities

### New Capabilities
- `editor-ui/links`: insert/edit hyperlinks (dialog + `<a>` round-trip through converter + sanitizer).
- `editor-ui/format-advanced`: text colour, background highlight, superscript/subscript.
- `editor-ui/table-cells`: merge/split cells, insert/delete row/column operations on the existing table UI.
- `editor-ui/insert-misc`: horizontal rule, page break, symbol/emoji insertion.
- `editor-ui/view-controls`: zoom, dark-mode toggle, fullscreen mode.
- `editor-ui/file-ops`: new blank document, export to PDF/ODT/HTML, print.
- `editor-ui/collab-presence`: remote cursor + avatar overlay driven by the existing CRDT presence channel.
- `editor-ui/status-bar`: word-count readout, save-status indicator, offline indicator.

### Modified Capabilities
<!-- none — all behaviour is net-new UI; underlying WOPI/CRDT/convert APIs are extended, not changed in contract -->

## Impact

- `web/index.html` — new toolbar groups, dialogs, status bar, view controls.
- `web/editor.js` — command wiring, dialogs, presence overlay, status updates, converter call sites.
- `web/style.css` — dialog/status-bar/view-mode styles, dark-mode variables.
- `src/editor/converter.py` — `html_to_docx`/`docx_to_html` (and ODT pair) learn `<a>`, colour spans, `<hr>`, page-break, table cell merges; export to ODT/HTML/PDF.
- `src/editor/sanitize.py` — allow `<a>` with safe URLs, `style="color/background"` with safe colours, `<hr>`, page-break; keep XSS closure.
- `src/editor/router.py` — new export endpoints (`/export/{fmt}`), new document scaffold, presence broadcast already exists.
- `src/editor/collab.py` — presence already emits cursors; UI overlay is the gap.
- Tests: Playwright UI checks + converter round-trip tests (mirror the image/table pattern we verified).
