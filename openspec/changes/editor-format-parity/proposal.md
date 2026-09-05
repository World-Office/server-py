# Change: Editor Format & Function Parity (DOCX / ODT)

## Context
The feature graph (`docs/office-research/feature-graph.md`, mirrored in chemie-neo4j)
enumerates **71 editing functions** required for full DOCX+ODT parity against the
ONLYOFFICE benchmark. Current IST status: **13 done, 2 partial, 56 missing**.

The gap is dominated by the **converters** (`src/editor/converter.py` for DOCX,
`src/editor/odt_converter.py` for ODT): they only round-trip bold/italic/underline,
images, headings, lists, and basic tables (merge/header). The sanitizer
(`src/editor/sanitize.py`) already whitelists color/background/font/align/border
styles, so the missing formatting is a **converter emit/parse gap**, not a sanitizer gap.

UI coverage was already specified in `editor-ui-completeness` (8 UI categories);
this change covers the **editing functions** (formatting, structure, objects, links,
references, header/footer, insert, view, file, collaboration, tools) end-to-end
(converter + UI).

## Goal
Make every one of the 71 functions possible to edit in both DOCX and ODT, verified by
failing-then-green contract tests (TDD).

## Scope
- IN: format emit/parse in `converter.py` and `odt_converter.py` (symmetric), UI
  controls in `web/editor.js`, collaboration extensions in `collab.py`, contract tests.
- OUT: server/infra, WOPI protocol changes, new persistence formats.

## Specs
editor-parity/inline-text, editor-parity/paragraph, editor-parity/lists,
editor-parity/structure, editor-parity/tables, editor-parity/objects,
editor-parity/links, editor-parity/references, editor-parity/header-footer,
editor-parity/insert, editor-parity/view, editor-parity/file,
editor-parity/collaboration, editor-parity/tools
