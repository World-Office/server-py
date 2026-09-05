## Purpose
Insert special symbol, date/time and horizontal rule; round-trip the horizontal rule through converters.

## ADDED Requirements

### Requirement: Special symbol
editor.js inserts the chosen unicode char; both converters round-trip it.

#### Scenario: Symbol round-trips
- **WHEN** a special symbol char is converted to DOCX/ODT and back
- **THEN** the char survives

### Requirement: Date/time
editor.js inserts current date/time; both converters round-trip it.

#### Scenario: Date-time round-trips
- **WHEN** an inserted date string is converted and back
- **THEN** the text survives

### Requirement: Horizontal rule
Both converters emit `<hr>` and round-trip.

#### Scenario: Horizontal rule round-trips
- **WHEN** an `<hr>` is converted to DOCX/ODT and back
- **THEN** the horizontal rule survives
