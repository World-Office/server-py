# Tasks: Cloud Editor — Complete Collaborative Office Editing

> **Status: CORE COMPLETE (verified end-to-end in a real browser).**
> OpenCloud/Nextcloud collaborative editing of office documents works:
> `mock WOPI host → docserver → browser edit → CRDT collab → save back to host`
> is proven by the Playwright E2E below. Two deviations from the original
> plan were made for pragmatism and are noted inline.

## Phase 0 — Mock WOPI host + contract tests (prove the loop) ✅
- [x] T0.1 contract test: mock WOPI host exposes CheckFileInfo/PutFile/Lock and the docserver's wopi_router satisfies it (RED→GREEN)
- [x] T0.2 implement mock WOPI host (`src/wopi/testhost.py`) + wire to docserver RemoteWopiClient (GREEN)
- [x] T0.3 contract test: open→edit→save round-trips office bytes from host through docserver back to host (RED→GREEN)
  - `tests/test_wopi_host_integration.py` (2 tests pass)

## Phase 1 — Editing surface + HTML/office round-trip ✅ (deviation: no Tiptap)
- [x] T1.1 `GET /api/documents/{doc_id}/html` returns semantic HTML; `/contents` stores it (GREEN)
- [x] T1.2 wire editor.js to `/html` + `/save`; convert HTML→office on save (GREEN)
  - **Deviation:** kept the existing rich `contenteditable` surface rather than
    porting to Tiptap — it already has tables, images, lists, undo/redo, find,
    and swapping risked regressing working features. Office conversion is done
    via `pandoc` (`src/editor/converter.py`).
- [x] T1.3 office→HTML→office round-trip keeps tables/lists/headings (RED→GREEN)
  - covered by `tests/test_converter.py` and `test_wopi_host_integration.py`

## Phase 2 — Real-time collaboration wiring ✅ (deviation: browser polls, not SSE)
- [x] T2.1 contract test: two clients editing same doc converge via CRDT hub (RED→GREEN)
  - `tests/test_collab_sync.py` (3 tests pass) + `tests/test_collab.py`
- [x] T2.2 wire browser collaboration: `POST /collab/sync` (plain text) + `/collab/presence`
  - **Deviation:** the browser polls `GET /collab/state` every ~400ms instead of
    consuming the `EventSource(/collab/stream)` SSE. Headless chromium's
    EventSource delivered only the first event per connection; polling is
    robust across embedded/headless contexts and keeps <1s convergence. The SSE
    endpoint remains available for push clients. Server-side convergence runs
    through the character `TextCRDT` in `src/editor/collab.py` (`sync_text`).
- [x] T2.3 render live remote text from TextCRDT (GREEN); presence badge shows
  connected collaborators (`#collab-badge`). Remote caret *positions* are shared
  via presence but not yet painted as colored carets (minor, out of scope).
- [x] T2.4 test: presence list reflects connected clients (RED→GREEN)

## Phase 3 — PostMessage bridge + lock lifecycle ✅
- [x] T3.1 contract test: editor notifies host via PostMessage on save/edit/close;
  lock acquired + refreshed + released (RED→GREEN)
  - verified in `tests/test_cloud_editor_e2e.py` (captures `window.__msgs`).
- [x] T3.2 implement PostMessage bridge in editor.js + lock lifecycle in router (GREEN)

## Phase 4 — UI feature specs → moved to `editor-ui-completeness` change
- [ ] T4.1 view-controls: zoom / dark-mode / fullscreen / print-layout
- [ ] T4.2 status-bar: connection / last-saved / lock / collaborators
- [ ] T4.3 table-cells: insert/edit/delete tables
- [ ] T4.4 links: insert/edit hyperlinks
- [ ] T4.5 insert-misc: image / horizontal-rule / special-char
- [ ] T4.6 format-advanced: headings / lists / alignment / color parity
  - These are tracked and implemented in the separate `editor-ui-completeness`
    change; many already exist in `web/editor.js`.

## Phase 5 — E2E + docs ✅
- [x] T5.1 Playwright E2E against mock WOPI host: two browser sessions edit the
  same document and converge in real time; save forwards bytes back to the host;
  host receives PostMessage bridge events.
  - `tests/e2e/test_cloud_editor_e2e.py` (passes; 358 tests green overall)
- [x] T5.2 run notes: mock WOPI host at `src/wopi/testhost.py`; E2E in
  `tests/e2e/`; design rationale in `docs/superpowers/specs/2026-08-26-cloud-editor-design.md`.
