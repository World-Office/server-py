## Purpose
Collaboration: comments, track-changes, presence cursors (supported), version history.

## ADDED Requirements

### Requirement: Comments
collab.py stores the comment; editor.js renders it; converters persist comment ranges.

#### Scenario: Comment round-trips
- **WHEN** a comment is added and the doc is saved/reloaded
- **THEN** the comment range and text survive

### Requirement: Track changes
When track-changes is enabled, collab.py records the change and the converter persists revision markup.

#### Scenario: Tracked edit round-trips
- **WHEN** an edit is made with track-changes on and saved/reloaded
- **THEN** the revision markup survives

### Requirement: Presence cursors
Other users' cursors render in real time (already supported via editor-ui-completeness/collab-presence).

#### Scenario: Presence cursor shows
- **WHEN** another user edits
- **THEN** their cursor is visible

### Requirement: Version history
collab.py lists snapshots; editor.js renders diff.

#### Scenario: Version history lists
- **WHEN** the user opens version history
- **THEN** prior snapshots are listed
