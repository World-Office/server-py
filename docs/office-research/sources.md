# Office-Research — External Sources & Resources (KG mirror)

> Curated sources for the DOCX/ODT editing benchmark, mirrored into chemie-neo4j
> as `Source` nodes (source:"external-reference"), all RELATED_TO ONLYOFFICE/FeatureSurface.

## Codebases (benchmarks / reference implementations)
| Source | URL | Relevance to World-Office |
|--------|-----|---------------------------|
| ONLYOFFICE/sdkjs | github.com/ONLYOFFICE/sdkjs | Engine (Word model) — already in KG as structure |
| ONLYOFFICE/web-apps | github.com/ONLYOFFICE/web-apps | Editor UI shell — already in KG |
| LibreOffice/core | github.com/LibreOffice/core | ODT-native reference + headless conversion |
| python-docx | github.com/python-openxml/python-docx | Python DOCX — directly relevant to our converter.py |
| odfpy | github.com/eea/odfpy | Python ODT — relevant to odt_converter.py |
| mammoth.js | github.com/mwilliamson/mammoth.js | DOCX→HTML round-trip (same mapping problem we solve) |
| docx4j | github.com/plutext/docx4j | Java DOCX object model |
| Pandoc | github.com/jgm/pandoc | Universal format converter (docx/odt/html) |
| Apache POI | github.com/apache/poi | Java OOXML (XWPF) |

## Standards / specifications
| Source | URL | Relevance |
|--------|-----|-----------|
| ECMA-376 (Office Open XML) | ecma-international.org/publications-and-standards/standards/ecma-376 | DOCX file format authority |
| OASIS OpenDocument (ODF) | docs.oasis-open.org/office | ODT file format authority |
| ISO/IEC 26300 | ISO ODF standard | ODT normative reference |

## Tools / libraries (for our stack)
| Source | URL | Relevance |
|--------|-----|-----------|
| weasyprint | weasyprint.org | HTML→PDF (export pipeline) |
| pdf-lib | pdf-lib.js.org | PDF generation |
| LibreOffice headless | libreoffice.org | Server-side docx/odt⇄pdf conversion |

## How to use
- Benchmark missing functions against ONLYOFFICE/sdkjs + LibreOffice Writer.
- For converter implementation, consult python-docx / odfpy for element mapping.
- For export, weasyprint / LibreOffice headless cover PDF.
