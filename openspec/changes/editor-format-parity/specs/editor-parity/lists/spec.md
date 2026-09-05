## Purpose
Support bullet, numbered and multilevel/outline lists round-tripping through DOCX and ODT.

## ADDED Requirements

### Requirement: Bullet and numbered lists
Both converters emit `<ul><li>` / `<ol><li>` and round-trip (already supported).

#### Scenario: Bullet list round-trips
- **WHEN** a bullet list is converted to DOCX/ODT and back
- **THEN** the list structure is preserved

#### Scenario: Numbered list round-trips
- **WHEN** a numbered list is converted to DOCX/ODT and back
- **THEN** the numbering is preserved

### Requirement: Multilevel / outline lists
Both converters emit nested `<ul>/<ol>` (or `data-level`) for deeper outline levels and round-trip; editor.js applies level on Tab/Shift-Tab.

#### Scenario: Multilevel list round-trips
- **WHEN** a list item at level 2 is converted to DOCX/ODT and back
- **THEN** the nesting level is preserved in HTML
