## Purpose
Round-trip full table formatting (borders, shading, size, caption, insert/add-row/col/split) through DOCX and ODT.

## ADDED Requirements

### Requirement: Table borders and shading
Both converters emit `border` and cell `background-color` and round-trip; sanitizer already permits `border`.

#### Scenario: Borders and shading round-trip
- **WHEN** a table with bordered cells and a shaded header cell is converted and back
- **THEN** borders and shading are preserved

### Requirement: Table width/height and caption
Both converters emit `width`/`height` and `<caption>` and round-trip.

#### Scenario: Size and caption round-trip
- **WHEN** a table with explicit width and a caption is converted and back
- **THEN** width and caption are preserved

### Requirement: Merge, header row, insert, add row/col, split
Merge (colspan/rowspan) and header row (`<th>`) already round-trip. Insert-table / add-row / add-col / split-cell also round-trip via editor.js.

#### Scenario: Insert and add-row round-trip
- **WHEN** editor.js inserts a 2x2 table and adds a row, then converted and back
- **THEN** the resulting table has 3 rows and round-trips
