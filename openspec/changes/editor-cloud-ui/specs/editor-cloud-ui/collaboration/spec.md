## Purpose
Collaboration panel: comments and version history, over the existing collab.py snapshots + presence.

## ADDED Requirements

### Requirement: Comments
Users can list and add comments; ranges persist through the converter.

#### Scenario: Add comment
- **WHEN** a user adds a comment on a selection and saves/reloads
- **THEN** the comment range and text survive

### Requirement: Version history
Prior snapshots are listed and can be restored.

#### Scenario: List versions
- **WHEN** the user opens version history
- **THEN** prior snapshots are listed and one can be restored

### Requirement: Presence cursors
Other users' cursors render in real time (already supported via editor-ui-completeness/collab-presence).

#### Scenario: Presence cursor shows
- **WHEN** another user edits
- **THEN** their cursor is visible
