## Purpose
Tools: find/replace (supported), spellcheck, word-count, undo/redo (supported), protect.

## ADDED Requirements

### Requirement: Find and replace
editor.js searches and replaces (already supported).

#### Scenario: Replace all
- **WHEN** the user replaces all occurrences of "foo" with "bar"
- **THEN** every occurrence is replaced

### Requirement: Spellcheck
editor.js underlines unknown words via a dictionary API.

#### Scenario: Misspelled word underlined
- **WHEN** a misspelled word is typed
- **THEN** it is underlined as incorrect

### Requirement: Word count
editor.js shows words/chars/pages.

#### Scenario: Word count shows
- **WHEN** the user opens word count
- **THEN** counts are displayed

### Requirement: Undo and redo
CRDT history applies (already supported).

#### Scenario: Undo restores
- **WHEN** the user undoes
- **THEN** the prior state is restored

### Requirement: Protect document
editor.js locks editing except allowed ranges.

#### Scenario: Protect locks
- **WHEN** the user enables protection
- **THEN** editing outside allowed ranges is blocked
