# World-Office — Required Editing Functions (DOCX / ODT parity graph)

> DAG of every editing function needed for full DOCX+ODT parity (ONLYOFFICE benchmark).
> Each function lists IST status (current World-Office converter) and REQUIRES edges
> to the modules that must support it (Converter-DOCX, Converter-ODT, UI, Sanitizer).
> Mirrored into chemie-neo4j (db chemie) as Function nodes + REQUIRES / ALIGNED_WITH / PART_OF.
> Last status sweep: 2026-08-26 (editor-format-parity change, suite 411 green).

Legend: ✅ = works now · ⚠️ = partial · ❌ = missing
Module deps: CD=converter.py(DOCX) · CO=odt_converter.py(ODT) · UI=editor.js · SA=sanitize.py

## 1. inline-text (PART_OF FeatureSurface)
- bold ✅ (CD,CO)
- italic ✅ (CD,CO)
- underline ✅ (CD,CO)
- strikethrough ✅ (CD,CO,UI; SA keeps strike/del)
- color ✅ (CD,CO)
- highlight ✅ (CD,CO)
- font-family ✅ (CD,CO)
- font-size ✅ (CD,CO)
- superscript ✅ (CD,CO)
- subscript ✅ (CD,CO)
- small-caps ✅ (CD,CO,UI)
- all-caps ✅ (CD,CO,UI)
- inline-code ✅ (CD,CO,UI)

## 2. paragraph (PART_OF FeatureSurface)
- align L/C/R/justify ✅ (CD,CO)
- line-spacing ✅ (CD,CO)
- indent L/R/first/hanging ✅ (CD,CO)
- spacing before/after ✅ (CD,CO; w:spacing ↔ fo:margin-top/bottom)
- paragraph-style ⚠️ (internal WO_Center/WO_Right/WO_A{n}; no user-named styles) — CD,CO,UI
- rtl ✅ (CD,CO,UI)
- page-break-before ✅ (CD,CO)

## 3. lists (PART_OF FeatureSurface)
- bullet ✅ (CD,CO)
- numbered ✅ (CD,CO)
- multilevel ✅ (CD,CO,UI; nested <ul>/<ol> ↔ List Bullet/Number [n] ↔ nested text:list; Tab/Shift-Tab indent)

## 4. structure (PART_OF FeatureSurface)
- headings ✅ (CD,CO)
- TOC ✅ (CD,CO,UI; <nav class="toc"> ↔ w:fldSimple TOC ↔ text:table-of-content; insert dialog for title)
- page-break ✅ (CD,CO,UI; <div class="page-break"> ↔ w:br page ↔ fo:break-before="page")
- section-break ✅ (CD,CO,UI; <hr class="section-break"> ↔ w:pPr/w:sectPr nextPage ↔ nested text:section; toolbar button)
- columns ✅ (CD,CO,UI; <section data-columns> ↔ w:sectPr/w:cols ↔ text:section text:columns; dialog for count+gap)

## 5. tables (PART_OF FeatureSurface)
- insert ✅ (UI dialog; roundtrip CD,CO)
- add-row/col ✅ (UI ops)
- merge ✅ (colspan/rowspan CD,CO)
- split ✅ (CD,CO,UI; <tr data-cantsplit> ↔ w:trPr/w:cantSplit)
- borders ❌ — REQUIRES CD,CO
- shading ❌ — REQUIRES CD,CO
- width/height ❌ — REQUIRES CD,CO
- header-row ✅ (th CD,CO)
- caption ✅ (CD,CO,UI; <figure><figcaption> ↔ w:tblCaption ↔ text:sequence-name)

## 6. objects (PART_OF FeatureSurface)
- image ✅ (insert UI + data-URI embed + resize UI (width/height px) round-tripping both formats; wrap = inline/as-char only) — REQUIRES UI,CD,CO
- shape ✅ (CD,CO,UI; <div class="object" data-type="shape"> ↔ wordprocessingShape ↔ draw:custom-shape placeholder; object dialog)
- textbox ✅ (CD,CO,UI; <div class="object" data-type="textbox"> ↔ wps:txbxContent ↔ draw:text-box; object dialog)
- chart ✅ (CD,CO,UI; <div class="object" data-type="chart"> ↔ c:chart placeholder ↔ chart:chart; object dialog)
- equation ✅ (CD,CO,UI; <div class="object" data-type="equation"> ↔ m:oMath ↔ math:math; object dialog)

## 7. links (PART_OF FeatureSurface)
- hyperlink ✅ (UI link dialog; text:a ↔ w:hyperlink; safe-scheme filter; boundaries preserved both formats) — CD,CO,UI
- bookmark ✅ (CD,CO,UI; <span class="bookmark" data-name> ↔ w:bookmarkStart/End ↔ text:bookmark-start/end; bookmark dialog)
- cross-reference ✅ (CD,CO,UI; <a href="#name"> ↔ w:hyperlink w:anchor ↔ text:bookmark-ref; cross-ref dialog)

## 8. references (PART_OF FeatureSurface)
- footnote ✅ (CD,CO: `<sup class="footnote-citation">[n]</sup>` + `<span class="footnote">` contract; DOCX real footnotes.xml part + w:footnoteReference, ODT text:note; no editor UI — static inline render)
- endnote ✅ (CD,CO: same mechanism via endnote-citation/endnote classes + endnotes.xml / text:note note-class=endnote)

## 9. header-footer (PART_OF FeatureSurface)
- header ✅ (CD,CO: `<header class="page-header">`; DOCX header1.xml part + sectPr headerReference; ODT master-page style:header)
- footer ✅ (CD,CO: `<footer class="page-footer">`; DOCX footer1.xml + footerReference; ODT master-page style:footer)
- page-number ✅ (CD,CO: `<span class="page-number">` ↔ DOCX PAGE field ↔ ODT text:page-number)

## 10. insert (PART_OF FeatureSurface)
- symbol ✅ (UI symbol picker; literal char round-trips) — UI
- date-time ✅ (UI btn-datetime inserts ISO date) — UI
- horizontal-rule ✅ (UI btn-hr; w:pBdr (CD) ↔ fo:border-bottom (CO)) — UI,CD,CO

## 11. view (PART_OF FeatureSurface)
- zoom ✅ (UI)
- dark-mode ✅ (UI)
- fullscreen ✅ (UI)
- print-layout ✅ (UI print stylesheet)

## 12. file (PART_OF FeatureSurface)
- new ✅ (UI)
- open ✅ (UI)
- save ✅ (WOPI)
- export-pdf/odt/html/docx ✅ (UI menu; server-side conversions) — UI,CD,CO
- print ✅ (UI)

## 13. collaboration (PART_OF FeatureSurface)
- comments ✅ (DOCX comments.xml + ODT office:annotation; editor.js span + margin notes)
- track-changes ✅ (CD,CO + UI: toggle, live recording, accept/reject review panel; DOCX w:ins/w:del + ODT change marks/tracked-changes registry)
- presence-cursor ✅ (collab-presence spec)
- version-history ❌ — REQUIRES collab

## 14. tools (PART_OF FeatureSurface)
- spellcheck ✅ (UI attribute)
- find-replace ✅
- word-count ✅ (UI status bar)
- undo-redo ✅
- protect ✅ (UI READ_ONLY handshake)

---
TOTAL functions: ~80 · ✅ ~44 · ⚠️ 5 · ❌ ~31
Converter gap (CD/CO) CLOSED — caption, split, columns, TOC, section-break and
objects (shape/textbox/chart/equation) all round-trip in both DOCX and ODT.
All editorial UI is implemented (bookmarks, cross-references, track-changes
toggle + accept/reject, comments sidebar + insert). Presence carets are
explicitly out of scope (see cloud-editor-complete).
