## Purpose
Lets the user insert and edit hyperlinks in the editor so documents can contain navigable references that survive save/load through the DOCX and ODT converters.

## ADDED Requirements

### Requirement: Insert hyperlink
The editor offers an "Insert link" control that opens a dialog where the user enters a URL and optional link text, then inserts an `<a>` element at the caret.

#### Scenario: Insert a link with text
- **WHEN** the user opens the link dialog, enters `https://graphwiz.ai` with text "Graphwiz" and confirms
- **THEN** an `<a href="https://graphwiz.ai">Graphwiz</a>` is inserted at the caret and is visible in the editable area

#### Scenario: Round-trips through save
- **WHEN** a document containing a hyperlink is saved and reloaded
- **THEN** the stored office file still contains the `<a href>` with the same URL and text

### Requirement: Safe URL scheme
Only `http`, `https`, `mailto` and relative URLs are accepted; `javascript:`, `data:` and other executable schemes are rejected or stripped.

#### Scenario: Block dangerous scheme
- **WHEN** the user enters `javascript:alert(1)` as the URL
- **THEN** the link is not inserted (or the scheme is stripped) and no script executes on click
