## Purpose
Adds document-level file operations — new blank document, export to PDF/ODT/HTML and print — so the editor is self-sufficient without leaving the page.

## ADDED Requirements

### Requirement: New blank document
A "New" control clears the editor to a blank document and seeds a fresh store entry (without destroying the current file until confirmed).

#### Scenario: Create new document
- **WHEN** the user chooses "New" and confirms
- **THEN** the editor shows an empty document ready to edit

### Requirement: Export
The editor can export the current document to ODT and HTML directly, and to PDF via the server; the exported bytes are a valid file of the chosen format.

#### Scenario: Export to ODT
- **WHEN** the user exports the document as ODT
- **THEN** they receive a valid `.odt` whose content matches the editor contents

#### Scenario: Export to PDF
- **WHEN** the user exports the document as PDF
- **THEN** they receive a valid PDF rendering of the current content

### Requirement: Print
A print control opens the browser print dialog with the document styled for paper.

#### Scenario: Print
- **WHEN** the user clicks print
- **THEN** the browser print dialog opens with the document content
