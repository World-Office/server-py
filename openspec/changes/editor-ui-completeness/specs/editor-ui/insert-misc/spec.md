## Purpose
Adds the common insert primitives users expect — horizontal rule, page break and a symbol/emoji picker — so the editor covers everyday document authoring.

## ADDED Requirements

### Requirement: Horizontal rule
A toolbar control inserts a `<hr>` at the caret; it round-trips through the converters.

#### Scenario: Insert a rule
- **WHEN** the user clicks "Insert horizontal rule"
- **THEN** an `<hr>` appears at the caret and survives save/reload

### Requirement: Page break
A control inserts a page break; in DOCX it maps to a paragraph/section break, in ODT to a `text:page-break` and in HTML to a page-break element, and round-trips.

#### Scenario: Insert a page break
- **WHEN** the user inserts a page break between two paragraphs
- **THEN** the saved office file contains a page break at that position

### Requirement: Symbol / emoji picker
A dialog lets the user pick a symbol or emoji and inserts it as text at the caret.

#### Scenario: Insert a symbol
- **WHEN** the user opens the symbol picker and chooses "§"
- **THEN** "§" is inserted at the caret and persists after save
