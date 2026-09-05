## Purpose
Adds advanced inline formatting — text colour, background highlight, superscript and subscript — so the editor matches standard office styling and the formatting survives the HTML↔office round-trip.

## ADDED Requirements

### Requirement: Text colour and highlight
The toolbar exposes a text-colour picker and a highlight (background-colour) picker backed by a fixed safe palette; applying one wraps the selection in a styled span.

#### Scenario: Apply colour and persist
- **WHEN** the user selects text and picks red from the colour palette
- **THEN** the selection becomes red and, after save/reload, the stored file keeps the red colour

### Requirement: Safe colour values
Only colours from an allowed list (or `#rrggbb` hex) are stored; `style="color"`/`style="background"` with `url(`, `expression(` or other tokens are rejected by the sanitizer.

#### Scenario: Reject expression-based colour
- **WHEN** a document carries `style="color:expression(alert(1))"`
- **THEN** the sanitizer drops the expression and keeps a safe colour or no colour

### Requirement: Superscript and subscript
The toolbar offers superscript/subscript toggles that map to `<sup>`/`<sub>` and round-trip through the converters.

#### Scenario: Insert superscript
- **WHEN** the user toggles superscript on selected "1"
- **THEN** it is wrapped in `<sup>1</sup>` and survives save/reload
