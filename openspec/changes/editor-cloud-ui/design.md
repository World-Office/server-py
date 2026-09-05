# Design: Editor Cloud-Integration UI

## Backend (router.py)
- `POST /api/documents/{doc_id}/export?format=pdf|odt|html|docx` — convert current
  HTML/DOCX to the requested format via converter (html_to_docx / html_to_odt / weasyprint
  for pdf) and return bytes with proper Content-Disposition. Reuses `_document_format`
  routing already present for save.
- `POST /api/documents/new` — create a blank DOCX/ODT template, register a session,
  return an editor URL (or WOPI PutRelativeFile against the cloud).
- `GET /api/documents/{doc_id}/print` — render to PDF for browser print (alias of export=pdf).

## Frontend (web/editor.js)
- File menu (top bar): New, Open (prompt WOPI src), Save (exists), Export (submenu
  pdf/odt/html/docx → calls export route, triggers download), Print (calls print route).
- View controls: zoom slider (CSS scale on editor surface), dark-mode toggle
  (html.theme class), fullscreen (requestFullscreen), print-layout toggle.
- Status bar (bottom): connection state, last-saved time, WOPI lock state,
  active collaborators count — driven by collab events + save responses.
- Collaboration panel: comments list + add, version history list + restore
  (backed by collab.py snapshots, already present).

## Tests (TDD)
- Backend: tests/test_router.py — export returns correct mime/bytes; new creates session.
- Frontend: tests/e2e — file menu / view controls / status bar elements exist and respond.
  (No JS unit infra; E2E via Playwright against live/dev server.)
