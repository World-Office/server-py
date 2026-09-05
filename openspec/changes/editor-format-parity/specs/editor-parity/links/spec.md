## Purpose
Round-trip links (hyperlink, bookmark, cross-reference) through DOCX and ODT; editor.js link dialog sets them.

## ADDED Requirements

### Requirement: Hyperlink
Both converters emit `<a href="...">` and round-trip; editor.js link dialog sets URL + text.

#### Scenario: Hyperlink round-trips
- **WHEN** a hyperlink is converted to DOCX/ODT and back
- **THEN** the `href` and text are preserved

### Requirement: Bookmark
Both converters emit a bookmark marker (`<a id>`) and round-trip.

#### Scenario: Bookmark round-trips
- **WHEN** a bookmark is converted and back
- **THEN** the bookmark id survives

### Requirement: Cross-reference
Both converters emit a reference marker to a heading/bookmark and round-trip.

#### Scenario: Cross-reference round-trips
- **WHEN** a cross-reference is converted and back
- **THEN** the reference target survives
