## Purpose
File operations: save (WOPI), new, open, export (PDF/ODT/HTML/DOCX), print.

## ADDED Requirements

### Requirement: Save
WOPI PutFile is called (already supported).

#### Scenario: Save persists
- **WHEN** the user saves
- **THEN** the document is written via WOPI PutFile

### Requirement: New document
editor.js resets to a blank DOCX/ODT template.

#### Scenario: New blank doc
- **WHEN** the user creates a new document
- **THEN** the editor shows a blank template

### Requirement: Open
editor.js loads another file via WOPI GetFile.

#### Scenario: Open loads file
- **WHEN** the user opens another file
- **THEN** its content loads into the editor

### Requirement: Export
docserver converts via the converter and serves the download (PDF/ODT/HTML/DOCX).

#### Scenario: Export to PDF
- **WHEN** the user exports to PDF
- **THEN** a PDF download is served

### Requirement: Print
editor.js opens the browser print dialog of the rendered doc.

#### Scenario: Print opens dialog
- **WHEN** the user prints
- **THEN** the browser print dialog opens
