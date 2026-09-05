## Purpose
Round-trip references (footnote, endnote) through DOCX and ODT; editor.js exposes insert controls.

## ADDED Requirements

### Requirement: Footnote
Both converters emit a footnote marker + footnote body and round-trip; editor.js exposes insert-footnote.

#### Scenario: Footnote round-trips
- **WHEN** a footnote is converted to DOCX/ODT and back
- **THEN** the marker and body survive

### Requirement: Endnote
Both converters emit an endnote marker + body and round-trip.

#### Scenario: Endnote round-trips
- **WHEN** an endnote is converted and back
- **THEN** the marker and body survive
