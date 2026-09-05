## Purpose
Extends the existing insert-table UI with cell/row/column editing operations so users can shape tables the way every office editor expects.

## ADDED Requirements

### Requirement: Merge and split cells
With one or more cells selected, the user can merge them into one cell and later split a merged cell back apart; the resulting structure round-trips through the converters.

#### Scenario: Merge selected cells
- **WHEN** the user selects two adjacent cells and chooses "Merge cells"
- **THEN** they become a single cell spanning both positions and the saved document keeps the merge

#### Scenario: Split a merged cell
- **WHEN** the user places the caret in a merged cell and chooses "Split cell"
- **THEN** the cell reverts to its original individual cells

### Requirement: Insert and delete rows/columns
The user can insert a row above/below and a column left/right, and delete the current row or column; the table stays valid and round-trips.

#### Scenario: Delete a column
- **WHEN** the user deletes the second column of a 3-column table
- **THEN** the saved table has two columns and all remaining cell content is preserved
