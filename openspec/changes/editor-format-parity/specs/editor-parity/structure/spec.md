## Purpose
Round-trip document structure elements (TOC, page-break, section-break, columns) through DOCX and ODT.

## ADDED Requirements

### Requirement: Headings
Both converters emit `<h1>`..`<h6>` and round-trip (already supported).

#### Scenario: Heading round-trips
- **WHEN** an `<h2>` is converted to DOCX/ODT and back
- **THEN** the heading level is preserved

### Requirement: Table of contents
Both converters emit a TOC placeholder element and round-trip.

#### Scenario: TOC round-trips
- **WHEN** a TOC field is converted to DOCX/ODT and back
- **THEN** the TOC placeholder survives

### Requirement: Page-break, section-break, columns
Both converters emit page-break / section marker / `column-count` and round-trip.

#### Scenario: Page-break and columns round-trip
- **WHEN** a manual page-break and a 2-column section are converted and back
- **THEN** both are preserved in HTML
