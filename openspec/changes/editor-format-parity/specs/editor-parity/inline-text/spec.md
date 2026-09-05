## Purpose
Round-trip inline run formatting (color, highlight, font, sup/sub, strike, caps, code) through DOCX and ODT converters.

## ADDED Requirements

### Requirement: Run text color
The converters emit and parse run text color so it survives HTML to DOCX and HTML to ODT round-trips.

#### Scenario: Color round-trips in DOCX
- **WHEN** a run with `color:#ff0000` is converted to DOCX and back to HTML
- **THEN** the resulting HTML keeps `style="color:#ff0000"`

#### Scenario: Color round-trips in ODT
- **WHEN** a run with `color:#ff0000` is converted to ODT and back to HTML
- **THEN** the resulting HTML keeps `style="color:#ff0000"`

### Requirement: Run highlight
Both converters emit `background-color` for highlight and round-trip it.

#### Scenario: Highlight round-trips
- **WHEN** a run with `background-color:#ffff00` is converted to DOCX/ODT and back
- **THEN** the highlight is preserved in HTML

### Requirement: Run font-family and font-size
Both converters emit `font-family`/`font-size` and round-trip.

#### Scenario: Font round-trips
- **WHEN** a run with `font-family:Arial;font-size:14pt` is converted and back
- **THEN** both properties survive the round-trip

### Requirement: Superscript, subscript, strikethrough, small-caps, all-caps, inline-code
Both converters emit `<sup>`/`<sub>`/`<strike>`/`font-variant:small-caps`/`text-transform:uppercase`/`<code>` and round-trip.

#### Scenario: Sup/sub/strike/caps/code round-trip
- **WHEN** runs with each of these properties are converted to DOCX/ODT and back
- **THEN** each property is preserved in HTML
