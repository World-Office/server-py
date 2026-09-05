## Purpose
Round-trip header, footer and page-number field through DOCX and ODT.

## ADDED Requirements

### Requirement: Header
Both converters emit header content and round-trip.

#### Scenario: Header round-trips
- **WHEN** a section header is converted to DOCX/ODT and back
- **THEN** the header content survives

### Requirement: Footer
Both converters emit footer content and round-trip.

#### Scenario: Footer round-trips
- **WHEN** a section footer is converted and back
- **THEN** the footer content survives

### Requirement: Page-number field
Both converters emit a page-number field and round-trip.

#### Scenario: Page-number round-trips
- **WHEN** a page-number field is converted and back
- **THEN** the field survives
