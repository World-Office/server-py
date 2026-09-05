## Purpose
Adds view-level controls (zoom, dark mode, fullscreen) so the editing surface adapts to the user's environment and preferences.

## ADDED Requirements

### Requirement: Zoom
The editor exposes zoom in/out (and a reset) that scales the editing surface without changing the stored document.

#### Scenario: Zoom in
- **WHEN** the user clicks zoom-in twice
- **THEN** the editable area is visually larger and the document content is unchanged on save

### Requirement: Dark mode
A toggle switches the editor between light and dark themes via CSS variables; the choice persists for the session.

#### Scenario: Toggle dark mode
- **WHEN** the user enables dark mode
- **THEN** the editor chrome and editing surface use dark colours and no content is altered

### Requirement: Fullscreen
A control expands the editor to fill the viewport and restores it on exit.

#### Scenario: Enter fullscreen
- **WHEN** the user clicks fullscreen
- **THEN** the editor occupies the full viewport and the toolbar remains usable
