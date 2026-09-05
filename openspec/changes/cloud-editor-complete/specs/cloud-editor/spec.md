# Capability: cloud-editor

## Functional Requirements

- **FR-1 (WOPI Server API):** The docserver SHALL implement `CheckFileInfo`, `GetFile`,
  `PutFile`, `Lock`, `Unlock`, `RefreshLock`, `GetLock` and `/hosting/discovery` conforming to
  the WOPI REST contract, consistent with `src/wopi/router.py`.
- **FR-2 (Mock WOPI host):** A mock WOPI host SHALL be provided for local E2E validation that
  emulates an OpenCloud/Nextcloud host (file store + token auth + lock state) so the full
  open→edit→save loop can be exercised without a full suite.
- **FR-3 (Editing surface):** The editor SHALL use Tiptap (ProseMirror) producing clean semantic
  HTML, round-tripping through the office↔HTML converter without loss of structure
  (headings, lists, tables).
- **FR-4 (Real-time collaboration):** Two or more clients editing the same document SHALL converge
  in real time via the existing `TextCRDT` + SSE (`/collab/stream`, `/collab/ops`, `/collab/presence`);
  remote cursors and live text SHALL be rendered.
- **FR-5 (PostMessage bridge):** The editor loaded in a host iframe SHALL notify the host of
  `edit`, `save`, and `close` via PostMessage; the WOPI lock SHALL be acquired on open, refreshed
  on activity, and released on close.
- **FR-6 (UI completeness):** The editor SHALL provide view controls (zoom, dark mode, fullscreen),
  a status bar (connection / last-saved / lock / collaborators), table editing, hyperlink editing,
  and insert-misc (image / horizontal rule / special char) per `editor-ui-completeness`.

## Validation

- pytest contract tests for WOPI endpoints + save round-trip (Phase 0, 1).
- Contract test for two-client convergence via collab endpoints (Phase 2).
- Playwright E2E against the mock WOPI host: open → edit → save → second client sees live edits
  and cursors (Phase 5).
