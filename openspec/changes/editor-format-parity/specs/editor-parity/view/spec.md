## Purpose
View controls (zoom, dark-mode, fullscreen, print-layout) are UI-only and do not alter the document.

## ADDED Requirements

### Requirement: Zoom
editor.js scales the editing surface without changing document content.

#### Scenario: Zoom changes scale only
- **WHEN** the user sets zoom to 150%
- **THEN** the visible surface scales but the saved document is unchanged

### Requirement: Dark mode
editor.js and index.html switch theme.

#### Scenario: Toggle dark mode
- **WHEN** the user toggles dark mode
- **THEN** the editor applies the dark theme

### Requirement: Fullscreen
editor.js requests fullscreen on the editor container.

#### Scenario: Enter fullscreen
- **WHEN** the user clicks fullscreen
- **THEN** the editor container enters fullscreen

### Requirement: Print layout
editor.js toggles page-width rendering.

#### Scenario: Toggle print layout
- **WHEN** the user toggles print layout
- **THEN** the editor switches between flow and page rendering
