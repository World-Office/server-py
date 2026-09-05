# Design: Cloud Editor — Complete Collaborative Office Editing

See `docs/superpowers/specs/2026-08-26-cloud-editor-design.md` for the full design.

Summary: complete the collaborative cloud editor on the existing `opencloud-docserver`
(FastAPI + WOPI Server API + `TextCRDT` collab engine + office↔HTML converter). Build a mock
WOPI host for E2E validation, integrate Tiptap as the editing surface, wire the existing
real-time collab engine into the editor, complete the PostMessage bridge + lock lifecycle, and
finish the `editor-ui-completeness` / `editor-format-parity` UI specs. TDD throughout.
