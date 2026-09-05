## Purpose
Full file operations against OpenCloud (WOPI): new, open, save (exists), export (pdf/odt/html/docx), print.

## ADDED Requirements

### Requirement: Export to PDF
The editor offers export-to-PDF; the backend converts the current document and returns PDF bytes.

#### Scenario: Export PDF
- **WHEN** a client POSTs `/api/documents/{doc_id}/export?format=pdf`
- **THEN** the response is `application/pdf` with `Content-Disposition: attachment` and non-empty bytes

### Requirement: Export to ODT / HTML / DOCX
The backend converts to the requested format and returns matching mime + bytes.

#### Scenario: Export ODT
- **WHEN** a client POSTs `/api/documents/{doc_id}/export?format=odt`
- **THEN** the response is `application/vnd.oasis.opendocument.text` with ODT bytes

#### Scenario: Export HTML
- **WHEN** a client POSTs `/api/documents/{doc_id}/export?format=html`
- **THEN** the response is `text/html` with the document HTML

#### Scenario: Export DOCX
- **WHEN** a client POSTs `/api/documents/{doc_id}/export?format=docx`
- **THEN** the response is `application/vnd.openxmlformats-officedocument.wordprocessingml.document` with DOCX bytes

### Requirement: New document
A blank DOCX/ODT template can be created and opened in the editor.

#### Scenario: Create new
- **WHEN** a client POSTs `/api/documents/new`
- **THEN** a blank session is created and an editor URL is returned

### Requirement: Print
The editor can produce a print-ready PDF and open the browser print dialog.

#### Scenario: Print route
- **WHEN** a client GETs `/api/documents/{doc_id}/print`
- **THEN** a PDF is returned (same as export=pdf)
