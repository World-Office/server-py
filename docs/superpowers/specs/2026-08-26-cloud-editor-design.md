# Cloud Editor — Complete Collaborative Office Editing (Design)

**Date:** 2026-08-26
**Status:** Approved (brainstorming complete; proceeding to implementation)

## Goal

A minimal, Stoic, single-process cloud editor that lets OpenCloud and Nextcloud users
collaboratively edit office documents through the WOPI protocol — built on the existing
`opencloud-docserver` FastAPI app, not a from-scratch rewrite.

## Decisions (from brainstorming)

- **Collaboration:** real-time via the *existing* `TextCRDT` + SSE collab engine (server-side
  already built in `src/editor/collab.py`); needs editor wiring, not a new engine. WOPI lock
  lifecycle still enforced for host safety.
- **Editing surface:** Tiptap (ProseMirror) — clean semantic HTML, good office round-trip, natural
  CRDT fit.
- **Integration validation:** WOPI-compliant + a lightweight **mock WOPI host** we write, exercised
  by Playwright E2E. Real OpenCloud/Nextcloud is a later validation step.

## Architecture

Single FastAPI app (`opencloud-docserver`):
- `src/wopi/router.py` — WOPI Server API (`CheckFileInfo`, `GetFile`, `PutFile`, `Lock`,
  `Unlock`, `RefreshLock`, `GetLock`) + `/hosting/discovery`.
- `src/editor/router.py` — editor page (`/editor/{doc_id}`), document API
  (`/html`, `/save`, `/export`, `/new`, `/contents`), collab SSE (`/collab/stream`, `/collab/ops`,
  `/collab/presence`, `/collab/resync`).
- `src/editor/collab.py` — `TextCRDT` + `CollabHub` (real-time engine, already present).
- `src/editor/converter.py` / `odt_converter.py` — office ↔ HTML.
- `src/editor/session.py` — `EditorSession`, `RemoteWopiClient`, `SessionRegistry`.
- Browser: editor loads in host iframe; REST+SSE to docserver; PostMessage to host.

## WOPI flow

1. Host calls `CheckFileInfo` (with `access_token`); loads `/editor/{doc_id}?access_token=…` in iframe.
2. Editor acquires WOPI lock; fetches document HTML (`GET /api/documents/{doc_id}/html`).
3. User edits in Tiptap. On save: HTML → office (converter) → `PUT` to host via `RemoteWopiClient`.
4. Lock refreshed on activity; released on close. PostMessage bridge notifies host of
   `edit`, `save`, `close`.

## Collaboration model

- `editor.js` opens `EventSource(/api/documents/{doc_id}/collab/stream)`; receives ops + presence.
- Local edits → `TextCRDT.local_insert/delete` → `POST /collab/ops`; remote ops integrated via
  `CollabHub.apply_ops`; remote cursors rendered from `/collab/presence`.
- Delivers true real-time multi-user editing (Q1 "A"), not just auto-save.

## Feature scope (OpenSpec)

Complete in-flight specs:
- `editor-ui-completeness`: view-controls (zoom, dark, fullscreen), status-bar (connection,
  saved, lock, collaborators), table-cells, links, insert-misc, format-advanced. File-ops (T6) done.
- `editor-format-parity`: tables/lists/headings fidelity through the converter.

## Validation

- Mock WOPI host (small FastAPI app, `src/wopi/testhost.py` or `tests/`).
- pytest contract tests: WOPI endpoints, save round-trip.
- Playwright E2E: open → edit → save round-trips to host; second client sees live edits/cursors.

## Out of scope (YAGNI)

- No new storage backend (host owns files).
- No separate CRDT server (in-process hub suffices for MVP).
- No converter re-architecture.

## Build order (TDD, red→green)

0. Mock WOPI host + contract tests (prove the loop).
1. Tiptap editing surface + HTML round-trip tests.
2. Collab wiring (SSE/ops/presence) + cursor rendering + tests.
3. PostMessage bridge + lock lifecycle + tests.
4. UI feature specs (view-controls, status-bar, tables, links, insert-misc, format-advanced).
5. E2E (Playwright against mock host) + docs.
