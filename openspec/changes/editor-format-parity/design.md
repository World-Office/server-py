# Design: Editor Format & Function Parity

## Reference artifacts
- Feature graph: `docs/office-research/feature-graph.md` (local) + chemie-neo4j
  (`Function` nodes, `source:"World-Office"`, `REQUIRES`/`PART_OF`/`ALIGNED_WITH`).
- IST analysis of converters: `_wrap_run_text` (converter.py ~274) only b/i/u;
  `_InlineRunBuilder` (~596) only b/i/u/img; `_add_styled_runs` (~671) only b/i/u.
  `odt_converter.py` `_wrap` (~448) / `_InlineRunBuilder` (~846) / `_styled_runs` (~922)
  symmetric gap.
- Sanitizer `sanitize.py:_sanitize_style` (~168) already whitelists
  color, background-color, font-family, font-size, font-weight, font-style,
  text-align, text-decoration, margins, padding, border, line-height.

## Approach (TDD)
1. For each function, write a **failing contract test** first
   (`tests/test_converter.py` for DOCX, `tests/test_odt_converter.py` for ODT)
   asserting HTML→format→HTML round-trip preserves the property.
2. Extend the converter's run/paragraph/table builders to emit + parse the property
   as inline `style="..."` or semantic elements (`<sup>`,`<sub>`,`<strike>`,`<mark>`,
   table `border`, cell `bgcolor`, etc.).
3. Extend `web/editor.js` exec/query commands + toolbar/dialogs to set the property.
4. Keep both converters **symmetric** (same property set, same HTML mapping).

## HTML mapping conventions (contract)
- run props → inline style: color→`color`, highlight→`background-color`,
  font-family→`font-family`, font-size→`font-size`, sup→`<sup>`, sub→`<sub>`,
  strike→`<strike>`, small-caps→`font-variant:small-caps`, all-caps→`text-transform:uppercase`,
  inline-code→`<code>`.
- paragraph props → inline style / attributes: line-spacing→`line-height`,
  indent→`margin-left/right;text-indent`, spacing→`margin-top/bottom`,
  rtl→`direction:rtl`, page-break-before→`page-break-before:always`.
- table props → `border`, cell `bgcolor` (shading), `width`/`height`.
- Sanitizer already permits all of the above.

## Out of scope for contracts
Pure UI-only functions (zoom, dark-mode, fullscreen, print-layout, file-new/open,
spellcheck, word-count, protect) get spec + UI task but no converter round-trip test.
