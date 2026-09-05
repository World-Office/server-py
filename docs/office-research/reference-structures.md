# Reference Code Structures — python-docx & LibreOffice Writer

> Detailed code structure of the two most relevant reference codebases, mirrored into
> chemie-neo4j as Module/Class nodes (source:"python-docx" / "libreoffice"),
> RELATED_TO ONLYOFFICE/FeatureSurface and ALIGNED_WITH World-Office functions.

## python-docx (DOCX, Python — directly relevant to converter.py)
Directory: python-docx
- Module: docx.document → Class: Document
- Module: docx.text.paragraph → Class: Paragraph, _Paragraph
- Module: docx.text.run → Class: Run, _Run
- Module: docx.text.font → Class: Font (bold, italic, underline, color, size, name)
- Module: docx.table → Class: Table, Cell, _Cell, _Row, _Column, _Table
- Module: docx.oxml → Class: OxmlElement, parse_xml, CT_Body
- Module: docx.oxml.text.paragraph → Class: CT_P, CT_R, CT_T, CT_PPr, CT_RPr
- Module: docx.oxml.table → Class: CT_Tbl, CT_Tr, CT_Tc, CT_TblPr
- Module: docx.oxml.shape → Class: CT_Blip, CT_Drawing
- Module: docx.section → Class: Section, Sections
- Module: docx.shape → Class: InlineShape
- Module: docx.styles → Class: Styles, Style
- Module: docx.enum.text → Enum: WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_BREAK
- Module: docx.enum.table → Enum: WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
- Module: docx.shared → Class: Pt, RGBColor, Length, Emu
- Module: docx.image → Class: Image, ImagePart

## LibreOffice Writer (ODT native + DOCX filter, C++)
Directory: LibreOffice/core/sw  (StarWriter)
- Module: sw/core → Class: SwDoc, SwPaM, SwPosition, SwNode, SwTextNode, SwTableNode, SwFrame
- Module: sw/doc → Class: SwXTextDocument, SwDocShell
- Module: sw/table → Class: SwTable, SwTableBox, SwTableLine, SwTableBoxFormat, SwTableFormat
- Module: sw/text → Class: SwTextFrame, SwTextShell, SwTextFormatColl
- Module: sw/layout → Class: SwRootFrame, SwPageFrame, SwFlyFrame
- Module: sw/filter → Class: SwFilter, SwReader, SwWriter
- Module: sw/filter/ww8 → Class: WW8Export, WW8Import (DOC/DOCX I/O)
- Module: sw/filter/xml → Class: XMLReader, XMLWriter (ODT I/O)
- Module: sw/inc → headers: fmt, frmatr, hintids

## Alignment with World-Office functions
- python-docx Run/Font → wo:bold, wo:italic, wo:underline, wo:color, wo:font-*
- python-docx Table/Cell → wo:table-*
- python-docx Document/Section → wo:header-footer, wo:structure
- LibreOffice SwTextNode → wo:inline-text, wo:paragraph
- LibreOffice SwTable → wo:table-*
- LibreOffice WW8/XML filter → wo: export/import (DOCX + ODT round-trip)
