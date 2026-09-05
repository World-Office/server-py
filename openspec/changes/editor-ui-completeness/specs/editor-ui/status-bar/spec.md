## Purpose
Adds a status/indicator area so the user gets live feedback on document size, save state and connectivity instead of only a save button.

## ADDED Requirements

### Requirement: Word count
The editor shows a live word (and character) count that updates as the user types.

#### Scenario: Count updates
- **WHEN** the user types five words
- **THEN** the status bar shows the updated word count

### Requirement: Save-status indicator
The editor shows whether content is saved, saving or dirty; it reflects the actual PUT to the WOPI host.

#### Scenario: Dirty then saved
- **WHEN** the user edits after a save
- **THEN** the indicator shows "unsaved", and after the next successful save shows "saved"

### Requirement: Offline indicator
When the editor cannot reach the host (or the service worker is offline), a clear offline indicator is shown and edits are queued locally.

#### Scenario: Go offline
- **WHEN** connectivity to the host is lost
- **THEN** the status bar shows an offline state and edits are retained for later sync
