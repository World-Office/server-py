## Purpose
Round-trip objects (image sizing/wrap, shape, textbox, chart, equation) through DOCX and ODT.

## ADDED Requirements

### Requirement: Image with size and wrap
Both converters emit `<img>` with `width`/`height`/wrap attributes and round-trip; editor.js exposes resize/position (partial now).

#### Scenario: Image size round-trips
- **WHEN** an image with width/height is converted to DOCX/ODT and back
- **THEN** the dimensions are preserved

### Requirement: Shape, textbox, chart, equation
Both converters emit an embed element (svg/object/data-uri) and round-trip; editor.js exposes an insert-object dialog.

#### Scenario: Object embed round-trips
- **WHEN** a chart/shape object is converted to DOCX/ODT and back
- **THEN** the object placeholder survives the round-trip
