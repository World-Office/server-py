## Context

The editor is a single FastAPI app serving `web/index.html` + `web/editor.js`
over WOPI (host mode forwards to OpenCloud via `RemoteWopiClient`). Two facts
shape this design:

1. **Round-trip is the hard part.** Every new element must survive
   `html → office → html`. We already proved the pattern for tables and
   images: the converter (`src/editor/converter.py`) emits/parses the office
   XML and `src/editor/sanitize.py` keeps the HTML safe on save. New inline
   elements (<a>, colour spans, `<hr>`, page break, `<sup>/<sub>`) follow the
   same shape — extend both sides, not just the UI.
2. **Presence already exists in the hub.** `src/editor/collab.py` broadcasts
   ops + presence cursors; the gap is purely the UI overlay, not the protocol.

Tooling reality: there is **no JS test framework** (no `package.json`, no
Vitest). UI is verified by Playwright against a local docserver (the pattern we
used in verification: upload a docx, open `/editor/{id}`, exercise the control,
assert round-trip). Keep that as the acceptance bar.

## Goals / Non-Goals

**Goals:**
- Close the visible gap between the current toolbar and a usable office editor.
- Every element must round-trip through DOCX **and** ODT converters + sanitizer.
- Reuse existing dialog/toolbar/i18n patterns (`data-cmd`, `data-i18n*`, aria-labels).

**Non-Goals:**
- New document *formats* (DOCX/ODT already cover it).
- Changing the WOPI or CRDT wire contracts.
- Rich comment threads / review mode (only lightweight presence cursors here).

## Decisions

- **One capability = one spec = one feature slice.** Implement per capability
  so each can be reviewed/tested independently (mirrors how the 39 tasks shipped).
- **Safe lists, not allow-lists-of-tags.** Extend `sanitize.py` with property
  whitelists: `style="color|background"` only from a fixed palette / `#rrggbb`;
  `<a>` only with `http(s)|mailto|relative` href (reuse `_is_safe_url` from the
  image fix). Keep the entity-decode + escape fix from the earlier XSS work.
- **Converter symmetry.** Add emit + parse handlers in both `html_to_docx`/
  `docx_to_html` and `html_to_odt`/`odt_to_html` so ODT stays first-class.
- **Colour as inline span**, not class, so it maps cleanly to `w:rPr/w:color`
  and `fo:text-color`.
- **Page break** → DOCX paragraph `w:br w:type="page"`, ODT `text:page-break`,
  HTML `page-break-after` on a marker element.
- **Presence overlay** rendered from the existing SSE presence channel; colour
  per `client_id` hash; no new backend endpoint.
- **Export** adds `GET /api/documents/{id}/export/{fmt}` (odt/html server-side;
  pdf via a headless render or `docx->pdf` lib if available, else HTML-print).

## Risks / Trade-offs

- **PDF export** may need a new dependency (e.g. a headless renderer). If none
  is available/lightweight, ship ODT+HTML first and defer PDF (mark as follow-up).
- **Symbol/emoji** insertion is just text; emoji rendering depends on the
  viewer font — acceptable.
- **Table merge/split** round-trip is the trickiest converter change (grid
  spans); reuse the existing `colspan`/`rowspan` support we already ship.
- **No JS unit tests** means UI regressions are caught only by Playwright; keep
  the round-trip assertion in every feature's Playwright check.
