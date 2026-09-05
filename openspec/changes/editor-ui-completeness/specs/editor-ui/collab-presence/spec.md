## Purpose
Surfaces live collaboration on top of the existing CRDT hub: remote carets and avatar labels so co-editors see each other's position in real time.

## ADDED Requirements

### Requirement: Remote carets
For every connected peer the editor renders a coloured caret at the peer's character position, updated as they type; carets disappear on leave.

#### Scenario: Peer caret appears
- **WHEN** a second editor joins the same document and moves the caret
- **THEN** the first editor shows a coloured caret at the peer's position

#### Scenario: Peer caret follows edits
- **WHEN** the peer inserts text
- **THEN** their caret moves with the inserted text and stays correct after the round-trip

### Requirement: Avatar labels
Each peer is shown with a stable colour and a short name/initial label; the local user has a distinct identity.

#### Scenario: Distinct identity
- **WHEN** two peers are connected
- **THEN** each sees the other's label in the peer's colour, with no collision
