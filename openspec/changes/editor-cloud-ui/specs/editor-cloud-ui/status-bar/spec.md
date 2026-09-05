## Purpose
Status bar showing connection state, last-saved time, WOPI lock state and active collaborators.

## ADDED Requirements

### Requirement: Connection and save state
The status bar reflects connection health and last successful save.

#### Scenario: Shows last-saved
- **WHEN** a save succeeds
- **THEN** the status bar shows "saved" with a timestamp

### Requirement: Lock state
The status bar reflects the WOPI lock held on the document.

#### Scenario: Shows lock
- **WHEN** the document is locked by this session
- **THEN** the status bar shows a locked indicator

### Requirement: Collaborators count
The status bar shows how many users are editing.

#### Scenario: Shows collaborators
- **WHEN** other users are connected
- **THEN** the status bar shows the active collaborator count
