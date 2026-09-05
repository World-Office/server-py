## Purpose
View controls: zoom, dark-mode, fullscreen, print-layout. UI-only, do not alter the document.

## ADDED Requirements

### Requirement: Zoom
A zoom control scales the editing surface without changing document content.

#### Scenario: Zoom changes scale only
- **WHEN** the user sets zoom to 150%
- **THEN** the visible surface scales but the saved document is unchanged

### Requirement: Dark mode
A toggle switches the editor theme.

#### Scenario: Toggle dark mode
- **WHEN** the user toggles dark mode
- **THEN** the editor applies the dark theme class

### Requirement: Fullscreen
A control requests fullscreen on the editor container.

#### Scenario: Enter fullscreen
- **WHEN** the user clicks fullscreen
- **THEN** the editor container enters fullscreen

### Requirement: Print layout
A toggle switches between flow and page-width rendering.

#### Scenario: Toggle print layout
- **WHEN** the user toggles print layout
- **THEN** the editor switches page-width rendering
