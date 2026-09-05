## Purpose
Round-trip paragraph-level formatting (line-spacing, indent, spacing, RTL, page-break, style) through DOCX and ODT converters.

## ADDED Requirements

### Requirement: Paragraph line-spacing
Both converters emit `line-height` and round-trip it.

#### Scenario: Line-spacing round-trips
- **WHEN** a paragraph with `line-height:1.5` is converted to DOCX/ODT and back
- **THEN** the line-height is preserved in HTML

### Requirement: Paragraph indent and spacing
Both converters emit `margin-left`/`margin-right`/`text-indent`/`margin-top`/`margin-bottom` and round-trip.

#### Scenario: Indent and spacing round-trip
- **WHEN** a paragraph with left indent, first-line indent and spacing-before is converted and back
- **THEN** all three properties survive the round-trip

### Requirement: RTL and page-break-before
Both converters emit `dir="rtl"`/`direction:rtl` and `page-break-before:always` and round-trip.

#### Scenario: RTL and page-break round-trip
- **WHEN** an RTL paragraph with page-break-before is converted and back
- **THEN** both properties survive the round-trip

### Requirement: Named paragraph style
Both converters emit `class="<stylename>"` for a paragraph style and round-trip.

#### Scenario: Paragraph style round-trips
- **WHEN** a paragraph bound to style "Heading 2" (via class) is converted and back
- **THEN** the class/style association is preserved
