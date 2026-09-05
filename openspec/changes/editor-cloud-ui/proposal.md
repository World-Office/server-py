# Change: Editor Cloud-Integration UI (feature parity)

## Context
The editor currently has minimal cloud-integration UI: `saveDocument()` (WOPI PutFile)
and `loadDocument()` (WOPI GetFile) work, but the full cloud-office surface is missing.
Feature graph (docs/office-research/feature-graph.md) marks these as missing:
wo:file-new, wo:file-open(extended), wo:export (pdf/odt/html/docx), wo:print,
wo:zoom, wo:dark-mode, wo:fullscreen, wo:print-layout, wo:comments, wo:version-history,
and a status-bar (sync/lock/connection) which is not even in the graph yet.

ONLYOFFICE (benchmark) and LibreOffice Writer (reference) both expose a full file menu,
collaboration panel and status bar when embedded in a cloud (Nextcloud/ownCloud/OpenCloud).
World-Office must reach that parity to be a usable cloud office editor.

## Goal
Full cloud-integration UI against OpenCloud (WOPI): file new/open/save/export/print,
view controls, collaboration (comments + version history), and a status bar —
each verified by a failing-then-green test (TDD).

## Scope
- IN: backend routes in router.py (export, new, print), UI in web/editor.js
  (file menu, view controls, status bar, collaboration panel), E2E/pytest tests.
- OUT: WOPI protocol internals (already implemented), converter format parity
  (covered by editor-format-parity), collaboration CRDT core (already in collab.py).

## Specs
editor-cloud-ui/file, editor-cloud-ui/view, editor-cloud-ui/collaboration,
editor-cloud-ui/status-bar
