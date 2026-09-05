# Tasks: Editor Cloud-Integration UI

## File operations
- [x] T1 contract test: POST /export?format=pdf returns application/pdf bytes (FAIL→GREEN)
- [x] T2 implement export route (pdf via weasyprint, odt/html/docx via converter)
- [x] T3 contract test: POST /export?format=odt returns ODT; html returns html; docx returns docx
- [x] T4 contract test: POST /new creates a blank session (FAIL→GREEN)
- [x] T5 implement new-document route + session registration
- [x] T6 editor.js file menu: New / Open / Export(submenu) / Print wired to routes
  (RED→GREEN: tests/test_file_menu.py; menu in index.html, cmds in editor.js)
- [ ] T7 E2E: file menu opens, export downloads, print dialog triggers

## View controls
- [ ] T8 editor.js: zoom slider scales surface (no doc change)
- [ ] T9 editor.js: dark-mode toggle adds theme class
- [ ] T10 editor.js: fullscreen + print-layout toggle
- [ ] T11 E2E: view controls exist and respond

## Collaboration
- [ ] T12 editor.js + collab.py: comments list/add UI over existing snapshots
- [ ] T13 editor.js: version-history list + restore
- [ ] T14 E2E: comments + version history render

## Status bar
- [ ] T15 editor.js: status bar shows connection / last-saved / lock / collaborators
- [ ] T16 E2E: status bar reflects save + lock state

## verification
- [ ] T17 run pytest + ruff; full local suite green
- [ ] T18 update KG feature-graph status (wo:file/export/print/view/collab → done)
