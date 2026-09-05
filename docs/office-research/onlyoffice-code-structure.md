# ONLYOFFICE — Code Structure Reference

> Reference architecture of ONLYOFFICE Docs (the open-source office suite:
> Document/Spreadsheet/Presentation editors + collaborative editing).
> Used as the benchmark for "what a complete DOCX/ODT editor must support".
> Mirrored into the chemie-lernen.org Neo4j code-knowledge-graph
> (container `chemie-neo4j`, db `chemie`) as Module/Class/Function nodes.

## Repositories (github.com/ONLYOFFICE)
- **DocumentServer** — meta repo / build orchestration
- **sdkjs** — the document engine (renderer + model), pure JS
- **web-apps** — the editor UI shells
- **core** — C++/JS shared libs (rendering, fonts, crypto)
- **server** — Node.js / .NET / Java document server (conversion, collaboration)

## sdkjs (the engine — relevant for DOCX/ODT)
- `sdkjs/common` — shared types, units, color utils
- `sdkjs/word` — **Word document engine** (Document, Paragraph, Run, Table, Field, History, Comments, Changes)
- `sdkjs/cell` — Spreadsheet engine
- `sdkjs/slide` — Presentation engine

### sdkjs/word model layers
- `Word/Document` — root; sections, body, styles
- `Word/Paragraph` — alignment, spacing, indents, numbering, borders, shading, style
- `Word/Run` — bold/italic/underline/strike, color, highlight, font(family/size), sup/sub, caps, links
- `Word/Table` — rows/cols, merge/split, borders, shading, cell props, caption
- `Word/Field` — hyperlink, bookmark, page-number, TOC field
- `Word/HeaderFooter` — header/footer, section breaks
- `Word/History` — undo/redo (command stack)
- `Word/Comments` / `Word/Changes` — comments + track-changes
- `Word/Shape`, `Word/Image`, `Word/Chart`, `Word/TextArt` — objects

## web-apps (UI shells)
- `web-apps/apps/documenteditor` — Document Editor (toolbar, left/right panels)
- `web-apps/apps/spreadsheeteditor` — Spreadsheet Editor
- `web-apps/apps/presentationeditor` — Presentation Editor
- `web-apps/common` — shared UI (toolbar builder, dialogs, i18n)

## Format support
- **Import**: DOCX, ODT, RTF, TXT, PDF (view), HTML, EPUB, CSV, XLSX, PPTX
- **Export**: DOCX, ODT, PDF, HTML, RTF, TXT, DOCXF/DOCT, EPUB

## Feature surface (DOCX/ODT editing) — the benchmark set
Inline text: bold, italic, underline, strike, color, highlight, font family/size,
superscript, subscript, small-caps, all-caps, inline code.
Paragraph: align L/C/R/justify, line-spacing, indents (left/right/first/hanging),
spacing before/after, paragraph styles, RTL, keep-together, page-break-before.
Lists: bullet, numbered, multilevel/outline.
Structure: headings, TOC, page break, section break, columns.
Tables: insert, row/col, merge/split, borders, shading, width/height, caption, header row.
Objects: image (resize/wrap/position), shape, textbox, chart, equation, smartart.
Links: hyperlink, bookmark, cross-reference.
References: footnote, endnote, citation, caption.
Header/Footer: header, footer, page number.
Insert: symbol, date/time, horizontal rule.
View: zoom, dark mode, fullscreen, print layout.
File: new, open, save, export (pdf/odt/html/docx), print.
Collaboration: comments, track-changes, presence cursors.
Other: spell-check, find/replace, word count, undo/redo, protect.
