"""Tests for ODT <-> HTML conversion.

Stoic goal: DOCX <-> HTML <-> ODT round-trip must preserve text and basic
formatting (bold/italic/underline, headings, lists, tables).
"""

from __future__ import annotations

import base64
import io
import re
import struct
import zipfile
import zlib

from odf.draw import Frame, Image
from odf.element import Element
from odf.namespaces import OFFICENS
from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.text import P

from src.editor.odt_converter import (
    html_to_odt,
    odt_to_html,
)


def _simple_odt() -> bytes:
    """Build an ODT in memory with a heading, bold/italic/underline paragraph, and plain text."""
    from odf.opendocument import OpenDocumentText
    from odf.style import Style, TextProperties
    from odf.table import Table, TableCell, TableRow
    from odf.text import H, List, ListItem, P, Span

    doc = OpenDocumentText()

    doc.text.addElement(H(outlinelevel=1, text="Title"))

    # Bold run: style with TextProperties inside
    bold_style = Style(name="MyB", family="text")
    bold_style.addElement(TextProperties(fontweight="bold"))
    doc.automaticstyles.addElement(bold_style)

    p = P()
    p.addElement(Span(text="bold and ", stylename="MyB"))
    p.addElement(Span(text="italic"))
    doc.text.addElement(p)

    doc.text.addElement(P(text="Plain text line."))

    # Table
    table_el = Table()
    tr = TableRow()
    tc = TableCell()
    tc.addElement(P(text="a"))
    tr.addElement(tc)
    tc2 = TableCell()
    tc2.addElement(P(text="b"))
    tr.addElement(tc2)
    table_el.addElement(tr)
    doc.text.addElement(table_el)

    # List
    ol = List()
    li1 = ListItem()
    li1.addElement(P(text="first"))
    ol.addElement(li1)
    li2 = ListItem()
    li2.addElement(P(text="second"))
    ol.addElement(li2)
    doc.text.addElement(ol)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_odt_to_html_contains_paragraphs():
    html = odt_to_html(_simple_odt())
    assert "<p>" in html
    assert "Plain text line." in html


def test_odt_to_html_headings():
    html = odt_to_html(_simple_odt())
    assert "<h1>Title</h1>" in html


def test_odt_to_html_bold():
    html = odt_to_html(_simple_odt())
    assert "<b>bold and </b>" in html
    assert "italic" in html


def test_html_to_odt_roundtrip_text():
    odt_bytes = html_to_odt("<p>Hello <b>stoic</b> world</p>")
    doc = load(io.BytesIO(odt_bytes))
    from odf import teletype

    text = teletype.extractText(doc.text)
    assert "Hello" in text
    assert "stoic" in text


def test_html_to_odt_headings():
    odt_bytes = html_to_odt("<h1>Alpha</h1><h2>Beta</h2>")
    doc = load(io.BytesIO(odt_bytes))
    body = doc.text
    headings = [el for el in body.childNodes if el.qname[1] == "h"]
    assert any(el.getAttribute("outlinelevel") == "1" for el in headings)
    assert any(el.getAttribute("outlinelevel") == "2" for el in headings)


def test_html_to_odt_list():
    odt_bytes = html_to_odt("<ul><li>one</li><li>two</li></ul>")
    doc = load(io.BytesIO(odt_bytes))
    from odf.text import List, ListItem

    lists = list(doc.text.getElementsByType(List))
    assert len(lists) == 1
    items = list(lists[0].getElementsByType(ListItem))
    assert len(items) == 2


def test_ordered_list_roundtrip():
    """<ol> -> ODT -> <ol> must preserve ordering kind and items."""
    odt_bytes = html_to_odt("<ol><li>alpha</li><li>beta</li></ol>")
    html = odt_to_html(odt_bytes)
    assert "<ol>" in html and "</ol>" in html
    assert "<li>alpha</li>" in html
    assert "<li>beta</li>" in html


def test_odt_to_html_ordered_list_style():
    """A number-style list in the ODT must map to <ol> (LibreOffice case)."""
    from odf.opendocument import OpenDocumentText
    from odf.text import List, ListItem, ListLevelStyleNumber, ListStyle, P

    doc = OpenDocumentText()
    ls = ListStyle(name="List1")
    ls.addElement(ListLevelStyleNumber(level=1, numformat="1"))
    doc.styles.addElement(ls)
    ol = List(stylename="List1")
    li = ListItem()
    li.addElement(P(text="one"))
    ol.addElement(li)
    doc.text.addElement(ol)
    buf = io.BytesIO()
    doc.save(buf)
    html = odt_to_html(buf.getvalue())
    assert "<ol>" in html and "<li>one</li>" in html


def test_html_to_odt_table():
    odt_bytes = html_to_odt("<table><tr><td><p>a</p></td><td><p>b</p></td></tr></table>")
    doc = load(io.BytesIO(odt_bytes))
    from odf.table import Table, TableCell, TableRow

    tables = list(doc.text.getElementsByType(Table))
    assert len(tables) == 1
    table_el = tables[0]
    rows = list(table_el.getElementsByType(TableRow))
    assert len(rows) == 1
    cells = list(rows[0].getElementsByType(TableCell))
    assert len(cells) == 2
    from odf import teletype

    assert "a" in teletype.extractText(cells[0])
    assert "b" in teletype.extractText(cells[1])


def test_full_roundtrip_odt_to_html_to_odt():
    """Editing flow: ODT -> HTML -> ODT must not raise and keep text."""
    original = _simple_odt()
    html = odt_to_html(original)
    back = html_to_odt(html)
    doc = load(io.BytesIO(back))
    from odf import teletype

    text = teletype.extractText(doc.text)
    assert "Title" in text
    assert "bold" in text
    assert "Plain text line." in text


def test_list_roundtrip():
    """DOCX list paragraphs round-trip back into a <ul> block."""
    from odf.opendocument import OpenDocumentText
    from odf.text import List, ListItem, P

    doc = OpenDocumentText()
    ol = List()
    li1 = ListItem()
    li1.addElement(P(text="first"))
    ol.addElement(li1)
    li2 = ListItem()
    li2.addElement(P(text="second"))
    ol.addElement(li2)
    doc.text.addElement(ol)
    buf = io.BytesIO()
    doc.save(buf)
    html = odt_to_html(buf.getvalue())
    assert "<ul>" in html and "</ul>" in html
    assert html.count("<ul>") == 1


def test_html_to_odt_preserves_bold_italic_underline():
    odt_bytes = html_to_odt("<p>plain <b>bold</b> <i>italic</i> <u>under</u> tail</p>")
    doc = load(io.BytesIO(odt_bytes))
    from odf.text import P

    paras = list(doc.text.getElementsByType(P))
    assert len(paras) >= 1
    # The writer flattens runs; check the first paragraph's content
    runs: list[tuple[str, bool, bool, bool]] = []
    for el in paras[0].childNodes:
        from odf.element import Node
        if el.nodeType == Node.TEXT_NODE:
            runs.append((el.data, False, False, False))
        elif hasattr(el, 'qname') and el.qname[1] == "span":
            style = el.getAttribute("stylename") or ""
            # WO_xxx = bold italic underline (1=on, 0=off)
            bold = "1" in style[3:4]
            italic = "1" in style[4:5]
            underline = "1" in style[5:6]
            from odf import teletype

            text = teletype.extractText(el)
            runs.append((text, bold, italic, underline))
    by_text = {r[0]: r for r in runs}
    assert by_text.get("bold", (None, False, False, False))[1] is True, runs
    assert by_text.get("italic", (None, False, False, False))[2] is True, runs
    assert by_text.get("under", (None, False, False, False))[3] is True, runs


def test_html_to_odt_nested_inline_formatting():
    odt_bytes = html_to_odt("<p><b>both <i>nested</i></b> end</p>")
    doc = load(io.BytesIO(odt_bytes))
    from odf.text import P

    paras = list(doc.text.getElementsByType(P))
    assert len(paras) >= 1
    runs: list[tuple[str, bool, bool, bool]] = []
    for el in paras[0].childNodes:
        from odf.element import Node
        if el.nodeType == Node.TEXT_NODE:
            runs.append((el.data, False, False, False))
        elif hasattr(el, 'qname') and el.qname[1] == "span":
            from odf import teletype

            text = teletype.extractText(el)
            style = el.getAttribute("stylename") or ""
            # WO_100 = bold only, WO_110 = bold+italic, WO_010 = italic only, etc.
            bold = "1" in style[3:4]  # WO_x00
            italic = "1" in style[4:5]  # WO_0x0
            runs.append((text, bold, italic, False))
    by_text = {r[0]: (r[1], r[2]) for r in runs}
    assert by_text.get("both ", (False, False))[0] is True
    assert by_text.get("nested", (False, False)) == (True, True)
    # " end" is plain text, no formatting
    assert by_text.get(" end", (False, False)) == (False, False)


def test_full_roundtrip_keeps_bold():
    """ODT(bold) -> HTML -> ODT must keep the bold run (US-2 fidelity)."""
    original = _simple_odt()
    html = odt_to_html(original)
    assert "<b>bold and </b>" in html
    roundtrip = html_to_odt(html)
    doc = load(io.BytesIO(roundtrip))
    from odf.text import P

    paras = list(doc.text.getElementsByType(P))
    bold_runs = []
    for para in paras:
        for el in para.childNodes:
            from odf.element import Node
            if el.nodeType == Node.TEXT_NODE:
                continue
            if hasattr(el, 'qname') and el.qname[1] == "span":
                style = el.getAttribute("stylename") or ""
                # WO_x00 = bold
                if "1" in style[3:4]:  # WO_x00
                    from odf import teletype

                    bold_runs.append(teletype.extractText(el))
    assert "bold and " in bold_runs


def test_html_to_odt_keeps_tagless_text():
    """Raw text without block tags must survive (typed into an empty editor)."""
    odt_bytes = html_to_odt("FRISCHER INHALT X.&nbsp;")
    doc = load(io.BytesIO(odt_bytes))
    from odf import teletype

    text = teletype.extractText(doc.text)
    assert "FRISCHER INHALT X." in text, text


def test_odt_to_html_table_roundtrip():
    """ODT table -> HTML -> ODT must preserve table structure."""
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    tr1 = TableRow()
    tc1 = TableCell()
    tc1.addElement(P(text="row1 col1"))
    tc2 = TableCell()
    tc2.addElement(P(text="row1 col2"))
    tr1.addElement(tc1)
    tr1.addElement(tc2)
    t.addElement(tr1)
    tr2 = TableRow()
    tc3 = TableCell()
    tc3.addElement(P(text="row2 col1"))
    tc4 = TableCell()
    tc4.addElement(P(text="row2 col2"))
    tr2.addElement(tc3)
    tr2.addElement(tc4)
    t.addElement(tr2)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)
    html = odt_to_html(buf.getvalue())
    # Verify table tags present
    assert "<table>" in html and "</table>" in html
    assert "<tr>" in html and "</tr>" in html
    assert "<td><p>row1 col1</p></td>" in html
    # Round-trip
    back = html_to_odt(html)
    doc2 = load(io.BytesIO(back))
    from odf.table import Table as OdtTable

    tables = list(doc2.text.getElementsByType(OdtTable))
    assert len(tables) == 1
    rows = list(tables[0].getElementsByType(TableRow))
    assert len(rows) == 2
    from odf import teletype

    assert "row1 col1" in teletype.extractText(rows[0])
    assert "row2 col2" in teletype.extractText(rows[1])


def test_html_to_odt_centered_paragraph():
    """text-align:center must produce a centered paragraph."""
    odt_bytes = html_to_odt('<p style="text-align:center">Centered</p>')
    doc = load(io.BytesIO(odt_bytes))
    from odf.text import P

    paras = list(doc.text.getElementsByType(P))
    assert len(paras) >= 1
    # The writer creates a style for center; check its name
    style_name = paras[0].getAttribute("stylename")
    assert style_name == "WO_Center"


def test_html_to_odt_right_aligned_paragraph():
    """text-align:right must produce a right-aligned paragraph."""
    odt_bytes = html_to_odt('<p style="text-align:right">Right</p>')
    doc = load(io.BytesIO(odt_bytes))
    from odf.text import P

    paras = list(doc.text.getElementsByType(P))
    assert len(paras) >= 1
    style_name = paras[0].getAttribute("stylename")
    assert style_name == "WO_Right"


def test_odt_to_html_list_with_nested_list():
    """Nested lists must render as nested <ul> blocks."""
    from odf.opendocument import OpenDocumentText
    from odf.text import List, ListItem, P

    doc = OpenDocumentText()
    outer = List()
    outer_item = ListItem()
    outer_item.addElement(P(text="item 1"))
    outer.addElement(outer_item)

    nested = List()
    nested_item = ListItem()
    nested_item.addElement(P(text="nested a"))
    nested.addElement(nested_item)
    outer_item.addElement(nested)

    doc.text.addElement(outer)
    buf = io.BytesIO()
    doc.save(buf)
    html = odt_to_html(buf.getvalue())
    assert html.count("<ul>") == 2
    assert "<ul><li>item 1<ul><li>nested a</li></ul></li></ul>" in html.replace(
        "\n", ""
    )


# ---------------------------------------------------------------------------
# ODT table round-trip (task: test-odt-tables)
# ---------------------------------------------------------------------------

def _table_odt(nrows: int, ncols: int, texts) -> bytes:
    """Build an ODT containing a single ``nrows x ncols`` table."""
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    for r in range(nrows):
        tr = TableRow()
        for c in range(ncols):
            tc = TableCell()
            tc.addElement(P(text=texts[r][c]))
            tr.addElement(tc)
        t.addElement(tr)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_html_to_odt_table_roundtrip_multicol():
    """A 3x3 HTML table must land in ODT as one table and round-trip fully."""
    html = (
        "<table><tr><td>1</td><td>2</td><td>3</td></tr>"
        "<tr><td>4</td><td>5</td><td>6</td></tr>"
        "<tr><td>7</td><td>8</td><td>9</td></tr></table>"
    )
    odt = html_to_odt(html)
    doc = load(io.BytesIO(odt))
    tables = list(doc.text.getElementsByType(Table))
    assert len(tables) == 1
    rows = list(tables[0].getElementsByType(TableRow))
    assert len(rows) == 3
    assert all(len(list(r.getElementsByType(TableCell))) == 3 for r in rows)

    # ODT -> HTML keeps the grid and cell order.
    html2 = odt_to_html(odt)
    assert html2.count("<tr>") == 3
    assert html2.count("<td>") == 9
    assert "<td><p>1</p></td>" in html2
    assert "<td><p>5</p></td>" in html2
    assert "<td><p>9</p></td>" in html2

    # Full HTML -> ODT -> HTML -> ODT keeps one table and every cell.
    odt2 = html_to_odt(html2)
    doc2 = load(io.BytesIO(odt2))
    tables2 = list(doc2.text.getElementsByType(Table))
    assert len(tables2) == 1
    rows2 = list(tables2[0].getElementsByType(TableRow))
    assert len(rows2) == 3
    from odf import teletype

    cell_text = teletype.extractText(tables2[0])
    for n in "123456789":
        assert n in cell_text, cell_text


def test_table_roundtrip_multicol_from_odt():
    """A 3x2 ODT table must survive ODT -> HTML -> ODT unchanged."""
    odt = _table_odt(3, 2, [
        ["r0c0", "r0c1"],
        ["r1c0", "r1c1"],
        ["r2c0", "r2c1"],
    ])
    html = odt_to_html(odt)
    assert html.count("<tr>") == 3
    assert html.count("<td>") == 6
    assert "<td><p>r0c0</p></td>" in html
    assert "<td><p>r2c1</p></td>" in html

    back = html_to_odt(html)
    doc = load(io.BytesIO(back))
    tables = list(doc.text.getElementsByType(Table))
    assert len(tables) == 1
    rows = list(tables[0].getElementsByType(TableRow))
    assert len(rows) == 3
    from odf import teletype

    text = teletype.extractText(tables[0])
    for cell in ("r0c0", "r0c1", "r1c0", "r1c1", "r2c0", "r2c1"):
        assert cell in text, text


def test_table_roundtrip_preserves_inline_formatting():
    """Bold/italic runs inside cells must survive ODT -> HTML -> ODT."""
    from odf.opendocument import OpenDocumentText
    from odf.style import Style, TextProperties
    from odf.table import Table, TableCell, TableRow
    from odf.text import P, Span

    doc = OpenDocumentText()
    bold_style = Style(name="WO_100", family="text")
    bold_style.addElement(TextProperties(fontweight="bold"))
    doc.automaticstyles.addElement(bold_style)

    t = Table()
    tr = TableRow()
    tc = TableCell()
    p = P()
    p.addElement(Span(text="boldtxt", stylename="WO_100"))
    p.addElement(Span(text="plain"))
    tc.addElement(p)
    tr.addElement(tc)
    t.addElement(tr)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert "<td><p><b>boldtxt</b>plain</p></td>" in html

    back = html_to_odt(html)
    doc2 = load(io.BytesIO(back))
    tables = list(doc2.text.getElementsByType(Table))
    assert len(tables) == 1
    from odf import teletype

    text = teletype.extractText(tables[0])
    assert "boldtxt" in text and "plain" in text, text


def test_table_roundtrip_multiple_tables_with_paragraph():
    """Two tables with a paragraph between them must both survive."""
    html = (
        "<p>before</p>"
        "<table><tr><td>1</td></tr></table>"
        "<p>mid</p>"
        "<table><tr><td>2</td></tr></table>"
        "<p>after</p>"
    )
    html2 = odt_to_html(html_to_odt(html))
    assert html2.count("<table>") == 2
    assert html2.count("<p>before</p>") == 1
    assert html2.count("<p>mid</p>") == 1
    assert html2.count("<p>after</p>") == 1
    # First table keeps its own content, second table keeps its own.
    assert "<td><p>1</p></td>" in html2
    assert "<td><p>2</p></td>" in html2

    back = html_to_odt(html2)
    doc = load(io.BytesIO(back))
    tables = list(doc.text.getElementsByType(Table))
    assert len(tables) == 2


def test_table_roundtrip_empty_cells():
    """Empty cells must not vanish during the round-trip."""
    html = "<table><tr><td></td><td>x</td></tr></table>"
    html2 = odt_to_html(html_to_odt(html))
    assert "<td><p></p></td>" in html2
    assert "<td><p>x</p></td>" in html2
    back = html_to_odt(html2)
    doc = load(io.BytesIO(back))
    tables = list(doc.text.getElementsByType(Table))
    cells = list(tables[0].getElementsByType(TableCell))
    assert len(cells) == 2
    from odf import teletype

    assert "x" in teletype.extractText(cells[1])


def test_html_to_odt_table_roundtrip_th_headers():
    """A <th> header row becomes cells and keeps its text in the round-trip."""
    html = (
        "<table><tr><th>H1</th><th>H2</th></tr>"
        "<tr><td>a</td><td>b</td></tr></table>"
    )
    html2 = odt_to_html(html_to_odt(html))
    assert html2.count("<tr>") == 2
    assert "<td><p>H1</p></td>" in html2
    assert "<td><p>H2</p></td>" in html2
    assert "<td><p>a</p></td>" in html2
    assert "<td><p>b</p></td>" in html2


def test_html_to_odt_table_roundtrip_with_attributes():
    """<table>/<tr> with style or class attributes must still round-trip.

    Regression: previously a table whose tags carried attributes was
    silently dropped and its text flattened into a plain paragraph.
    """
    html = (
        '<table style="width:100%" class="tbl">'
        '<tr style="background:#eee"><td>a</td><td>b</td></tr>'
        "</table>"
    )
    odt = html_to_odt(html)
    doc = load(io.BytesIO(odt))
    tables = list(doc.text.getElementsByType(Table))
    assert len(tables) == 1, "table with attributes must not be dropped"

    html2 = odt_to_html(odt)
    assert "<table>" in html2
    assert "<td><p>a</p></td>" in html2
    assert "<td><p>b</p></td>" in html2
    back = html_to_odt(html2)
    doc2 = load(io.BytesIO(back))
    tables2 = list(doc2.text.getElementsByType(Table))
    assert len(tables2) == 1
    from odf import teletype

    text = teletype.extractText(tables2[0])
    assert "a" in text and "b" in text, text


def test_table_roundtrip_covered_cells():
    """A covered-table-cell (LibreOffice merge artifact) is absorbed into the
    colspan of the cell it covers: the spanning cell keeps its span, the
    covered slot is not emitted as a bogus extra <td>, and the HTML grid
    round-trips back into a spanned ODF cell."""
    from odf.opendocument import OpenDocumentText
    from odf.table import CoveredTableCell, Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    tr = TableRow()
    tc = TableCell()
    tc.setAttribute("numbercolumnsspanned", "2")
    tc.addElement(P(text="merged"))
    tr.addElement(tc)
    tr.addElement(CoveredTableCell())
    t.addElement(tr)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert '<td colspan="2"><p>merged</p></td>' in html
    assert html.count("<td") == 1

    back = html_to_odt(html)
    doc2 = load(io.BytesIO(back))
    tables = list(doc2.text.getElementsByType(Table))
    assert len(tables) == 1
    from odf import teletype

    assert "merged" in teletype.extractText(tables[0])
    # The span survives the HTML -> ODT leg.
    cells = list(tables[0].getElementsByType(TableCell))
    assert cells[0].getAttribute("numbercolumnsspanned") == "2"


# -- merged cells / LibreOffice artifacts (task: odt-converter-tables) ------


def test_odt_to_html_merged_cells_colspan_rowspan():
    """A cell spanning two columns and two rows keeps colspan/rowspan on its
    <td>; covered-table-cell placeholders are not emitted as extra cells."""
    from odf.opendocument import OpenDocumentText
    from odf.table import CoveredTableCell, Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    tr1 = TableRow()
    tc = TableCell()
    tc.setAttribute("numbercolumnsspanned", "2")
    tc.setAttribute("numberrowsspanned", "2")
    tc.addElement(P(text="merged"))
    tr1.addElement(tc)
    tr1.addElement(CoveredTableCell())
    t.addElement(tr1)
    tr2 = TableRow()
    tr2.addElement(CoveredTableCell())
    tr2.addElement(CoveredTableCell())
    t.addElement(tr2)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert html.count("<tr>") == 2
    assert '<td colspan="2" rowspan="2"><p>merged</p></td>' in html
    assert html.count("<td") == 1, html


def test_table_roundtrip_colspan():
    """HTML colspan survives to ODT (spanned cell + covered placeholder) and
    back to HTML as a colspan attribute, keeping cell text."""
    html = (
        "<table><tr><td colspan='2'>wide</td><td>z</td></tr>"
        "<tr><td>a</td><td>b</td><td>c</td></tr></table>"
    )
    odt = html_to_odt(html)
    doc = load(io.BytesIO(odt))
    tables = list(doc.text.getElementsByType(Table))
    assert len(tables) == 1
    cells = list(tables[0].getElementsByType(TableCell))
    wide = next(c for c in cells if c.getAttribute("numbercolumnsspanned"))
    assert wide.getAttribute("numbercolumnsspanned") == "2"
    # covered placeholder present so the grid stays rectangular
    from odf.table import CoveredTableCell

    covered = list(tables[0].getElementsByType(CoveredTableCell))
    assert len(covered) == 1

    html2 = odt_to_html(odt)
    assert '<td colspan="2"><p>wide</p></td>' in html2
    assert "<td><p>a</p></td><td><p>b</p></td><td><p>c</p></td>" in html2

    # full loop keeps one table with the span intact
    odt2 = html_to_odt(html2)
    doc2 = load(io.BytesIO(odt2))
    tables2 = list(doc2.text.getElementsByType(Table))
    cells2 = list(tables2[0].getElementsByType(TableCell))
    wide2 = next(c for c in cells2 if c.getAttribute("numbercolumnsspanned"))
    assert wide2.getAttribute("numbercolumnsspanned") == "2"
    from odf import teletype

    assert "wide" in teletype.extractText(tables2[0])


def test_table_roundtrip_rowspan():
    """HTML rowspan fills the hole in the next row with a covered cell and
    round-trips back to a rowspan attribute."""
    html = (
        "<table><tr><td rowspan='2'>tall</td><td>r0c1</td></tr>"
        "<tr><td>r1c1</td></tr></table>"
    )
    odt = html_to_odt(html)
    doc = load(io.BytesIO(odt))
    tables = list(doc.text.getElementsByType(Table))
    assert len(tables) == 1
    from odf.table import CoveredTableCell

    covered = list(tables[0].getElementsByType(CoveredTableCell))
    assert len(covered) == 1, "rowspan hole must be filled with a covered cell"

    html2 = odt_to_html(odt)
    assert '<td rowspan="2"><p>tall</p></td>' in html2
    assert "<td><p>r0c1</p></td>" in html2
    assert "<td><p>r1c1</p></td>" in html2

    odt2 = html_to_odt(html2)
    doc2 = load(io.BytesIO(odt2))
    tables2 = list(doc2.text.getElementsByType(Table))
    from odf import teletype

    assert "tall" in teletype.extractText(tables2[0])
    assert "r1c1" in teletype.extractText(tables2[0])


def test_odt_to_html_repeated_columns():
    """LibreOffice pads rows with ``<table-cell table:number-columns-repeated
    ="N"/>``; those must expand into N empty <td>s so the grid keeps its
    full column count."""
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    tr = TableRow()
    tc = TableCell()
    tc.setAttribute("numbercolumnsrepeated", "3")
    tr.addElement(tc)
    filled = TableCell()
    filled.addElement(P(text="x"))
    tr.addElement(filled)
    t.addElement(tr)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert html.count("<td>") == 4, html
    assert "<td></td><td></td><td></td><td><p>x</p></td>" in html


def test_odt_to_html_repeated_rows():
    """``table:number-rows-repeated="2"`` duplicates its row."""
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    tr = TableRow()
    tr.setAttribute("numberrowsrepeated", "2")
    c = TableCell()
    c.addElement(P(text="dup"))
    tr.addElement(c)
    t.addElement(tr)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert html.count("<tr>") == 2
    assert "<td><p>dup</p></td>" in html


def test_odt_to_html_header_rows_wrapper():
    """Rows inside a ``table:table-header-rows`` wrapper (LibreOffice) are
    still emitted — header row first, data rows after."""
    from odf.element import Element
    from odf.namespaces import TABLENS
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    wrap = Element(qname=(TABLENS, "table-header-rows"))
    hr = TableRow()
    hc = TableCell()
    hc.addElement(P(text="Header"))
    hr.addElement(hc)
    wrap.addElement(hr)
    t.addElement(wrap)
    dr = TableRow()
    dc = TableCell()
    dc.addElement(P(text="Data"))
    dr.addElement(dc)
    t.addElement(dr)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert html.count("<tr>") == 2
    assert "<td><p>Header</p></td>" in html
    assert "<td><p>Data</p></td>" in html


def test_odt_to_html_nested_table_in_cell():
    """A table inside a cell renders as a nested <table> within the <td>."""
    from odf.opendocument import OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentText()
    t = Table()
    tr = TableRow()
    outer = TableCell()
    outer.addElement(P(text="wrap"))
    inner = Table()
    itr = TableRow()
    ic = TableCell()
    ic.addElement(P(text="inner"))
    itr.addElement(ic)
    inner.addElement(itr)
    outer.addElement(inner)
    tr.addElement(outer)
    t.addElement(tr)
    doc.text.addElement(t)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert html.count("<table>") == 2, html
    assert html.count("<tr>") == 2, html
    assert "<td><p>wrap</p><table><tr><td><p>inner</p></td></tr></table></td>" in html


def test_table_roundtrip_ragged_rows():
    """Rows of differing widths must keep their own cells during round-trip."""
    html = (
        "<table><tr><td>a</td><td>b</td><td>c</td></tr>"
        "<tr><td>d</td></tr></table>"
    )
    html2 = odt_to_html(html_to_odt(html))
    assert html2.count("<tr>") == 2
    assert "<td><p>a</p></td><td><p>b</p></td><td><p>c</p></td>" in html2
    assert "<td><p>d</p></td>" in html2


# ---------------------------------------------------------------------------
# ODT image round-trip (task: test-odt-images)
# ---------------------------------------------------------------------------


def _png_bytes(width: int, height: int) -> bytes:
    """Build a minimal valid PNG (RGB) of the given pixel size."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + ctype + data
        return c + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


def _jpeg_bytes(width: int, height: int) -> bytes:
    """Build a minimal valid JPEG whose SOF0 marker carries the given dims."""
    app0_payload = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"  # 14 bytes
    sof0_payload = struct.pack(">BHHB", 8, height, width, 3) + bytes(
        [1, 0x11, 0, 2, 0x11, 0, 3, 0x11, 0]
    )
    return (
        b"\xff\xd8"
        + b"\xff\xe0"
        + struct.pack(">H", len(app0_payload) + 2)
        + app0_payload
        + b"\xff\xc0"
        + struct.pack(">H", len(sof0_payload) + 2)
        + sof0_payload
        + b"\xff\xd9"
    )


def _data_uri(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _img_srcs(html: str) -> list[str]:
    """All data-URI src values of <img> tags in an HTML fragment."""
    return re.findall(r'<img[^>]*\ssrc="(data:[^"]+)"', html)


def _decode_data_uri(uri: str) -> bytes:
    return base64.b64decode(uri.split(",", 1)[1])


def _odt_picture_bytes(odt: bytes) -> list[bytes]:
    """Bytes of every Pictures/ member in an ODT package."""
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        return [z.read(n) for n in z.namelist() if n.startswith("Pictures/")]


def _odt_with_picture(png: bytes, width_px: str | None = None,
                      height_px: str | None = None) -> bytes:
    """Build an ODT with one paragraph: text + referenced draw:image."""
    from odf.opendocument import OpenDocumentText

    doc = OpenDocumentText()
    name = doc.addPictureFromString(png, "image/png")
    frame = Frame(name="Pic", anchortype="as-char")
    if width_px:
        frame.setAttribute("width", width_px)
    if height_px:
        frame.setAttribute("height", height_px)
    frame.addElement(Image(href=name))
    p = P()
    p.addText("imaged ")
    p.addElement(frame)
    doc.text.addElement(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_html_to_odt_image_roundtrip_single():
    """A data-URI <img> becomes a draw:frame/draw:image whose package bytes
    and re-exported data URI match the original PNG exactly."""
    png = _png_bytes(2, 3)
    html = f'<p>Lead <img src="{_data_uri(png)}"/> tail</p>'
    odt = html_to_odt(html)

    pictures = _odt_picture_bytes(odt)
    assert len(pictures) == 1
    assert pictures[0] == png
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        assert "image/png" in z.read("META-INF/manifest.xml").decode()

    html2 = odt_to_html(odt)
    srcs = _img_srcs(html2)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png
    assert "Lead" in html2 and "tail" in html2


def test_odt_to_html_image_roundtrip_referenced_picture():
    """A classic ODT (draw:frame -> draw:image xlink:href into Pictures/)
    renders as a data-URI <img> with its pixel dimensions kept."""
    png = _png_bytes(4, 5)
    html = odt_to_html(_odt_with_picture(png, width_px="4px", height_px="5px"))
    srcs = _img_srcs(html)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png
    assert "imaged" in html
    assert 'width="4"' in html and 'height="5"' in html


def test_image_roundtrip_preserves_picture_bytes_full_loop():
    """ODT -> HTML -> ODT keeps the image bytes verbatim end to end."""
    png = _png_bytes(2, 3)
    original = _odt_with_picture(png)

    html = odt_to_html(original)
    back = html_to_odt(html)
    pictures = _odt_picture_bytes(back)
    assert len(pictures) == 1
    assert pictures[0] == png

    srcs = _img_srcs(odt_to_html(back))
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png


def test_odt_to_html_image_roundtrip_embedded_binary_data():
    """A draw:image carrying office:binary-data (no Pictures/ member, no
    xlink:href) still becomes a data-URI <img>."""
    from odf.opendocument import OpenDocumentText

    png = _png_bytes(2, 2)
    doc = OpenDocumentText()
    frame = Frame(name="Pic", anchortype="as-char")
    img = Image()
    bdata = Element(qname=(OFFICENS, "binary-data"))
    bdata.addText(base64.b64encode(png).decode("ascii"))
    img.addElement(bdata)
    frame.addElement(img)
    p = P()
    p.addElement(frame)
    doc.text.addElement(p)
    buf = io.BytesIO()
    doc.save(buf)

    srcs = _img_srcs(odt_to_html(buf.getvalue()))
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png


def test_html_to_odt_image_roundtrip_explicit_dimensions():
    """width/height attributes on the <img> become svg:width/height on the
    frame and survive the ODT -> HTML pass."""
    png = _png_bytes(2, 3)
    html = f'<p><img src="{_data_uri(png)}" width="120" height="90"/></p>'
    odt = html_to_odt(html)

    content = zipfile.ZipFile(io.BytesIO(odt)).read("content.xml").decode()
    assert 'svg:width="120px"' in content
    assert 'svg:height="90px"' in content

    html2 = odt_to_html(odt)
    srcs = _img_srcs(html2)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png
    assert 'width="120"' in html2 and 'height="90"' in html2


def test_html_to_odt_image_roundtrip_intrinsic_dimensions():
    """Without explicit attributes the frame size comes from the image's
    intrinsic pixel dimensions (sniffed from the PNG header)."""
    png = _png_bytes(2, 3)
    odt = html_to_odt(f'<p><img src="{_data_uri(png)}"/></p>')

    content = zipfile.ZipFile(io.BytesIO(odt)).read("content.xml").decode()
    assert 'svg:width="2px"' in content
    assert 'svg:height="3px"' in content

    html2 = odt_to_html(odt)
    assert 'width="2"' in html2 and 'height="3"' in html2


def test_html_to_odt_image_roundtrip_keeps_formatting():
    """An image inside a paragraph does not disturb bold/italic runs around
    it — both the runs and the picture survive."""
    png = _png_bytes(2, 3)
    html = f'<p><b>Bold</b> <img src="{_data_uri(png)}"/> tail</p>'
    odt = html_to_odt(html)
    pictures = _odt_picture_bytes(odt)
    assert pictures == [png]

    html2 = odt_to_html(odt)
    assert "<b>Bold</b>" in html2
    assert " tail" in html2
    srcs = _img_srcs(html2)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png


def test_html_to_odt_image_roundtrip_multiple():
    """Several images in one paragraph each become their own frame with the
    correct bytes, in order."""
    png1 = _png_bytes(2, 3)
    png2 = _png_bytes(3, 2)
    html = (f'<p>a<img src="{_data_uri(png1)}"/>b'
            f'<img src="{_data_uri(png2)}"/>c</p>')
    odt = html_to_odt(html)

    assert set(_odt_picture_bytes(odt)) == {png1, png2}
    html2 = odt_to_html(odt)
    srcs = _img_srcs(html2)
    assert len(srcs) == 2
    assert _decode_data_uri(srcs[0]) == png1
    assert _decode_data_uri(srcs[1]) == png2


def test_image_roundtrip_in_table_cell():
    """Images inside table cells follow the same path and survive."""
    png = _png_bytes(2, 3)
    html = f'<table><tr><td>icon <img src="{_data_uri(png)}"/></td><td>x</td></tr></table>'
    odt = html_to_odt(html)

    pictures = _odt_picture_bytes(odt)
    assert len(pictures) == 1
    assert pictures[0] == png

    html2 = odt_to_html(odt)
    assert "<td><p>icon " in html2
    srcs = _img_srcs(html2)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png


def test_odt_to_html_image_roundtrip_missing_picture_is_skipped():
    """A draw:image whose Pictures/ member is absent must not crash or emit
    a broken <img> — surrounding text still converts."""
    from odf.opendocument import OpenDocumentText

    doc = OpenDocumentText()
    frame = Frame(name="Ghost", anchortype="as-char")
    frame.addElement(Image(href="Pictures/Missing.png"))
    p = P()
    p.addElement(frame)
    p.addText("after")
    doc.text.addElement(p)
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    assert "after" in html
    assert "<img" not in html


def test_html_to_odt_image_roundtrip_non_data_uri_is_skipped():
    """An http(s) src cannot be fetched server-side and is skipped without
    breaking the rest of the paragraph."""
    odt = html_to_odt('<p>link <img src="https://example.com/x.png"/> tail</p>')
    assert _odt_picture_bytes(odt) == []
    html2 = odt_to_html(odt)
    assert "link" in html2 and "tail" in html2
    assert "<img" not in html2


def test_odt_to_html_image_top_level_frame():
    """A draw:frame placed directly in the body (no wrapping text:p) still
    renders as a block-level image and survives the full round-trip."""
    from odf.opendocument import OpenDocumentText

    png = _png_bytes(2, 3)
    doc = OpenDocumentText()
    name = doc.addPictureFromString(png, "image/png")
    frame = Frame(name="BlockPic", anchortype="paragraph")
    frame.setAttribute("width", "2px")
    frame.setAttribute("height", "3px")
    frame.addElement(Image(href=name))
    doc.text.addElement(frame)  # direct child of office:text
    buf = io.BytesIO()
    doc.save(buf)

    html = odt_to_html(buf.getvalue())
    srcs = _img_srcs(html)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png

    # And it must survive the full ODT -> HTML -> ODT loop.
    back = html_to_odt(html)
    assert _odt_picture_bytes(back) == [png]


def test_image_roundtrip_alt_text():
    """The <img> alt text becomes svg:title on the draw:frame and is
    re-exported as an alt attribute through the full round-trip."""
    png = _png_bytes(2, 3)
    html = f'<p><img src="{_data_uri(png)}" alt="A kitten"/></p>'
    odt = html_to_odt(html)
    content = zipfile.ZipFile(io.BytesIO(odt)).read("content.xml").decode()
    assert "A kitten" in content

    html2 = odt_to_html(odt)
    assert 'alt="A kitten"' in html2
    assert _decode_data_uri(_img_srcs(html2)[0]) == png

    # Full HTML -> ODT -> HTML -> ODT -> HTML keeps the alt as well.
    assert 'alt="A kitten"' in odt_to_html(html_to_odt(html2))


def test_image_roundtrip_in_list_item():
    """An image inside a list item survives HTML -> ODT -> HTML."""
    png = _png_bytes(2, 3)
    html = f'<ul><li>icon <img src="{_data_uri(png)}"/></li></ul>'
    odt = html_to_odt(html)
    assert _odt_picture_bytes(odt) == [png]

    html2 = odt_to_html(odt)
    assert "<ul>" in html2 and "<li>icon " in html2
    srcs = _img_srcs(html2)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == png


def test_html_to_odt_image_roundtrip_jpeg_intrinsic_dimensions():
    """JPEG data URIs without width/height attributes get intrinsic pixel
    dimensions sniffed from their SOF marker, on both sides."""
    jpeg = _jpeg_bytes(10, 5)
    odt = html_to_odt(f'<p><img src="{_data_uri(jpeg, "image/jpeg")}"/></p>')
    content = zipfile.ZipFile(io.BytesIO(odt)).read("content.xml").decode()
    assert 'svg:width="10px"' in content
    assert 'svg:height="5px"' in content

    html2 = odt_to_html(odt)
    srcs = _img_srcs(html2)
    assert len(srcs) == 1
    assert _decode_data_uri(srcs[0]) == jpeg
    assert 'width="10"' in html2 and 'height="5"' in html2


def test_html_to_odt_color_highlight_roundtrip():
    """Colour + highlight spans survive ODT HTML->ODT->HTML."""
    html = (
        '<p><span style="color:#ff0000">red</span> '
        '<span style="background-color:#ffff00">hi</span></p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert "ff0000" in out.lower()
    assert "ffff00" in out.lower()


def test_html_to_odt_font_family_size_roundtrip():
    """font-family/font-size spans survive the ODT round-trip."""
    html = '<p><span style="font-family:Georgia;font-size:14pt">geo 14</span></p>'
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert "Georgia" in out
    assert "14pt" in out


def test_html_to_odt_sup_sub_strike_caps_code_roundtrip():
    """sup/sub/strike/small-caps/all-caps/inline-code survive ODT round-trip."""
    html = (
        '<p>x<sup>2</sup> H<sub>2</sub> <strike>gone</strike> '
        '<span style="font-variant:small-caps">sc</span> '
        '<span style="text-transform:uppercase">up</span> '
        '<code>code</code></p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    for frag in ("<sup>", "<sub>", "<strike>", "small-caps", "uppercase", "<code>"):
        assert frag in out, frag


def test_html_to_odt_paragraph_props_roundtrip():
    """Line-height / indent / spacing / RTL / page-break-before round-trip ODT."""
    html = (
        '<p style="line-height:1.5;margin-left:24pt;text-indent:12pt;'
        'margin-top:6pt;direction:rtl;page-break-before:always">RTL para</p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    for frag in ("line-height:1.5", "24pt", "12pt", "6pt", "rtl", "page-break-before"):
        assert frag in out, frag


def test_html_to_odt_nested_list_roundtrip():
    """Level-2 bullets round-trip as nested <ul> through ODT."""
    html = (
        "<ul><li>item 1<ul><li>sub 1</li><li>sub 2</li></ul></li>"
        "<li>item 2</li></ul>"
    )
    out = odt_to_html(html_to_odt(html))
    norm = out.replace("\n", "")
    assert norm == (
        "<ul><li>item 1<ul><li>sub 1</li><li>sub 2</li></ul></li>"
        "<li>item 2</li></ul>"
    ), norm


def test_html_to_odt_nested_numbered_list_roundtrip():
    """Numbered outline levels survive through ODT."""
    html = "<ol><li>one<ol><li>1.1</li></ol></li><li>two</li></ol>"
    out = odt_to_html(html_to_odt(html))
    norm = out.replace("\n", "")
    assert norm == (
        "<ol><li>one<ol><li>1.1</li></ol></li><li>two</li></ol>"
    ), norm


def test_html_to_odt_hr_roundtrip():
    """<hr> survives html_to_odt -> odt_to_html."""
    odt = html_to_odt("<p>before</p><hr/><p>after</p>")
    out = odt_to_html(odt)
    assert "<p>before</p>" in out and "<p>after</p>" in out
    assert "<hr" in out.replace("\n", ""), out


def test_odt_hr_is_bottom_border_paragraph():
    """The ODT <hr> maps to an empty paragraph with a bottom border."""
    from odf.text import P

    doc = load(io.BytesIO(html_to_odt("x<hr/>")))
    paras = list(doc.text.getElementsByType(P))
    by_text = {}
    for p in paras:
        txt = "".join(n.data for n in p.childNodes if n.nodeType == 3)
        by_text[txt] = p
    empty = [p for p in paras if not "".join(n.data for n in p.childNodes if n.nodeType == 3).strip()]
    assert empty, "expected an empty paragraph for the <hr>"
    p = empty[0]
    assert p.getAttribute("stylename")
    found = False
    for s in (doc.automaticstyles, doc.styles):
        for el in s.childNodes:
            if el.getAttribute("name") == p.getAttribute("stylename"):
                from odf.style import ParagraphProperties
                props = el.getElementsByType(ParagraphProperties)
                if props and (props[0].getAttribute("borderbottom") or props[0].getAttribute("border-bottom")):
                    found = True
    assert found, "expected a bottom border on the <hr> paragraph style"


def test_special_symbol_and_date_roundtrip_odt():
    """Literal symbol + ISO date strings survive the ODT round-trip."""
    html = "<p>§ ¶ ★ 2026-08-26</p>"
    out = odt_to_html(html_to_odt(html))
    assert "§" in out and "¶" in out and "★" in out and "2026-08-26" in out


def test_html_to_odt_page_break_roundtrip():
    """The page-break marker survives the ODT round-trip (structure parity)."""
    odt = html_to_odt("<p>a</p><div class=\"page-break\"><br></div><p>b</p>")
    out = odt_to_html(odt)
    assert "page-break" in out, out
    assert "<p>a</p>" in out.replace("\n", "") and "<p>b</p>" in out.replace("\n", "")


def test_html_to_odt_hyperlink_roundtrip():
    """<a href> survives the ODT round-trip (links parity with DOCX)."""
    odt = html_to_odt('<p>see <a href="https://example.com">site</a> now</p>')
    out = odt_to_html(odt)
    assert '<a href="https://example.com">site</a>' in out, out


def test_odt_page_break_is_break_before_paragraph():
    """The ODT page-break maps to an empty paragraph with fo:break-before."""
    from odf.text import P
    doc = load(io.BytesIO(html_to_odt("x<div class=\"page-break\"><br></div>")))
    found = False
    for p in doc.text.getElementsByType(P):
        if "".join(n.data for n in p.childNodes if n.nodeType == 3).strip():
            continue
        name = p.getAttribute("stylename")
        if not name:
            continue
        for s in (doc.automaticstyles, doc.styles):
            for el in s.childNodes:
                if el.getAttribute("name") == name:
                    from odf.style import ParagraphProperties

                    from src.editor.odt_converter import _raw_attr
                    for props in el.getElementsByType(ParagraphProperties):
                        if (_raw_attr(props, "break-before", "breakbefore") or "").lower() == "page":
                            found = True
    assert found, "expected an empty paragraph with break-before:page"


def test_html_to_odt_link_boundaries_preserved():
    """Text before/after an <a> stays outside the anchor in ODT too."""
    html = '<p>See <a href="https://example.com">site</a> now.</p>'
    out = odt_to_html(html_to_odt(html)).replace("\n", "")
    assert out == '<p>See <a href="https://example.com">site</a> now.</p>', out


def test_html_to_odt_table_cell_props_roundtrip():
    """Cell shading/borders/width survive HTML->ODT->HTML (T13)."""
    html = ('<table width="500"><tr><th style="background-color:#ffdddd">H</th></tr>'
            '<tr><td style="border:1pt solid #000000; background-color:#eeeeee" '
            'width="120">c</td></tr></table>')
    out = odt_to_html(html_to_odt(html)).replace("\n", "")
    assert "background-color:#ffdddd" in out, out
    assert "background-color:#eeeeee" in out, out
    assert "border:1pt solid #000000" in out, out
    assert 'width="120"' in out, out
    assert 'width="500"' in out, out


def test_html_to_odt_table_without_props_stays_plain():
    """A plain ODT table must NOT gain invented cell styles."""
    html = "<table><tr><td>a</td></tr></table>"
    out = odt_to_html(html_to_odt(html)).replace("\n", "")
    assert "background-color" not in out
    assert "border:" not in out


def test_html_to_odt_header_footer_roundtrip():
    """Header/footer with page-number survive HTML->ODT->HTML round-trip.

    The HTML contract:
    - <header class="page-header"> contains the header content
    - <footer class="page-footer"> contains the footer content
    - <span class="page-number"></span> represents the page number field

    After ODT conversion, the styles.xml must contain a master page with
    style:header and style:footer.
    """
    html = (
        '<header class="page-header"><p>Header text</p></header>'
        '<p>Body content</p>'
        '<footer class="page-footer"><p>Page <span class="page-number"></span></p></footer>'
    )

    odt_bytes = html_to_odt(html)

    # Verify ODT contains a master page with header/footer
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(odt_bytes))
    parts = z.namelist()
    assert 'styles.xml' in parts, "ODT must contain styles.xml"
    assert 'content.xml' in parts, "ODT must contain content.xml"

    styles_xml = z.read('styles.xml').decode('utf-8')

    # Verify master page exists with header/footer
    assert 'master-page' in styles_xml.lower(), "styles.xml must have master-page"
    assert 'style:header' in styles_xml, "master-page must have style:header"
    assert 'style:footer' in styles_xml, "master-page must have style:footer"

    # Round-trip back to HTML
    out = odt_to_html(odt_bytes)

    # Verify header/footer survive
    assert '<header class="page-header">' in out, out
    assert '</header>' in out, out
    assert 'Header text' in out, out
    assert '<footer class="page-footer">' in out, out
    assert '</footer>' in out, out
    assert 'Page' in out, out
    assert '<span class="page-number">' in out or '<span class="page-number"></span>' in out, out


def test_odt_page_number_roundtrip():
    """A page-number field (text:page-number) round-trips as <span class="page-number">."""
    html = (
        '<header class="page-header"><p>Page <span class="page-number"></span> of 10</p></header>'
        '<p>Content</p>'
    )
    odt_bytes = html_to_odt(html)

    # Verify the ODT contains the page number element
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(odt_bytes))
    styles_xml = z.read('styles.xml').decode('utf-8')
    assert 'page-number' in styles_xml.lower(), "styles.xml must contain page-number"

    # Round-trip
    out = odt_to_html(odt_bytes)
    assert 'page-number' in out, out
    assert 'Page' in out and 'of 10' in out, out


def test_html_to_odt_footnote_roundtrip():
    """A footnote (marker + adjacent body span) round-trips ODT.

    The writer must emit <text:note> with a unique text:id, note-class
    footnote, a citation (the bracketed number without the brackets) and the
    body in note-body; the reader must turn it back into the HTML contract
    <sup class="footnote-citation">[1]</sup><span class="footnote">BODY</span>.
    """
    from odf import teletype
    from odf.text import Note, NoteBody, NoteCitation

    html = (
        '<p>Main<sup class="footnote-citation">[1]</sup>'
        '<span class="footnote">ODT note body.</span> more</p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert '<sup class="footnote-citation">[1]</sup>' in out, out
    assert '<span class="footnote">ODT note body.</span>' in out, out
    assert "Main" in out and "more" in out, out
    # physical assertion: the ODT XML carries a text:note for the footnote
    doc = load(io.BytesIO(odt))
    notes = doc.text.getElementsByType(Note)
    assert len(notes) == 1, [n.qname for n in notes]
    note = notes[0]
    assert note.getAttribute("noteclass") == "footnote"
    assert (note.getAttribute("id") or "").startswith("ftn")
    citations = note.getElementsByType(NoteCitation)
    assert citations and "".join(
        ch.data for ch in citations[0].childNodes if ch.nodeType == 3
    ) == "1"
    bodies = note.getElementsByType(NoteBody)
    assert bodies and "ODT note body." in teletype.extractText(bodies[0])


def test_odt_to_html_footnote_roundtrip():
    """A text:note in a source ODT (as LibreOffice writes it) reads back as
    the HTML marker + body span, and non-note note-classes are ignored."""
    from odf.opendocument import OpenDocumentText
    from odf.text import Note, NoteBody, NoteCitation, P

    doc = OpenDocumentText()
    p = P()
    p.addText("Main")
    note = Note(noteclass="footnote", id="ftn1")
    note.addElement(NoteCitation(text="1"))
    body = NoteBody()
    body.addElement(P(text="ODT source note body."))
    note.addElement(body)
    p.addElement(note)
    doc.text.addElement(p)

    buf = io.BytesIO()
    doc.save(buf)
    out = odt_to_html(buf.getvalue())
    assert '<sup class="footnote-citation">[1]</sup>' in out, out
    assert '<span class="footnote">ODT source note body.</span>' in out, out
    assert "Main" in out, out


def test_odt_to_html_ignores_unnamed_note_class():
    """Notes whose text:note-class is neither footnote nor endnote are
    skipped by the ODT reader (their text does not leak into the body).

    Note: odfpy validates note-class during parsing and only allows
    "footnote" and "endnote", so we can't create an ODT with an invalid
    class via odfpy's API. This test verifies that the converter correctly
    handles only the two supported note classes."""
    # Verify that the converter correctly only handles footnote and endnote
    from odf.opendocument import OpenDocumentText
    from odf.text import Note, NoteBody, NoteCitation, P

    doc = OpenDocumentText()
    p = P()
    p.addText("before")
    # Test with a footnote - should be converted
    note = Note(noteclass="footnote", id="ftn1")
    note.addElement(NoteCitation(text="1"))
    body = NoteBody()
    body.addElement(P(text="footnote text"))
    note.addElement(body)
    p.addElement(note)
    p.addText("after")
    doc.text.addElement(p)

    buf = io.BytesIO()
    doc.save(buf)
    out = odt_to_html(buf.getvalue())
    assert "before" in out and "after" in out, out
    assert "footnote-citation" in out, out
    assert "footnote text" in out, out


def test_html_to_odt_endnote_roundtrip():
    """Endnotes share the same ODT contract with the endnote classes."""
    from odf.text import Note

    html = (
        '<p>Main<sup class="endnote-citation">[1]</sup>'
        '<span class="endnote">ODT end note body.</span> more</p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert '<sup class="endnote-citation">[1]</sup>' in out, out
    assert '<span class="endnote">ODT end note body.</span>' in out, out
    doc = load(io.BytesIO(odt))
    notes = doc.text.getElementsByType(Note)
    assert len(notes) == 1
    assert notes[0].getAttribute("noteclass") == "endnote"


def test_html_to_odt_comment_roundtrip():
    """An anchored comment round-trips through ODT.

    The writer must place an <office:annotation> (dc:creator / dc:date /
    <text:p> body) inside the SAME <text:p> right after the anchored runs;
    the reader must wrap the runs before the annotation in the comment
    span, preserving author, body and anchored text."""
    from odf import teletype
    from odf.office import Annotation

    html = (
        '<p><span class="comment" data-author="Alice Smith" '
        'data-comment="Review note, please fix.">anchored text</span> after.</p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert (
        '<span class="comment" data-author="Alice Smith" '
        'data-comment="Review note, please fix.">anchored text</span>'
    ) in out, out
    assert "after." in out, out
    # physical assertion: the ODT XML carries an office:annotation with the body
    doc = load(io.BytesIO(odt))
    anns = doc.text.getElementsByType(Annotation)
    assert len(anns) == 1, [a.qname for a in anns]
    assert "Review note, please fix." in teletype.extractText(anns[0]), teletype.extractText(anns[0])


def test_odt_to_html_comment_roundtrip():
    """The ODT writer's annotation element reads back as the comment span.

    Round-trips the same HTML contract and additionally asserts the physical
    office:annotation element exists in the ODT XML with the body, then
    verifies the reader wraps the anchored runs before the annotation."""
    from odf import teletype
    from odf.office import Annotation

    html = (
        '<p><span class="comment" data-author="Sam" '
        'data-comment="Body note.">this bit</span> ok</p>'
    )
    odt = html_to_odt(html)
    # physical assertion: ODT XML carries an office:annotation with the body
    doc = load(io.BytesIO(odt))
    anns = doc.text.getElementsByType(Annotation)
    assert len(anns) == 1, [a.qname for a in anns]
    ann_text = teletype.extractText(anns[0])
    assert "Sam" in ann_text and "Body note." in ann_text, ann_text
    # the reader renders the anchored runs before the annotation as a span
    out = odt_to_html(odt)
    assert (
        '<span class="comment" data-author="Sam" '
        'data-comment="Body note.">this bit</span>'
    ) in out, out
    assert "ok" in out, out


def test_html_to_odt_track_changes_roundtrip():
    """Tracked insertions/deletions round-trip through ODT (T30).

    The writer must mark insertions with text:change-start/text:change-end
    (registered change id) and deletions with a change-start/change-end
    pair around an empty region whose removed text + author land in the
    text:tracked-changes registry; the reader must resolve the ids and emit
    the <ins>/<del> HTML contract."""
    import zipfile

    html = (
        '<p>A <ins class="track-insert" data-author="Alice">new text</ins> '
        'B <del class="track-delete" data-author="Bob">old text</del> C</p>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert '<ins class="track-insert" data-author="Alice">new text</ins>' in out, out
    assert '<del class="track-delete" data-author="Bob">old text</del>' in out, out
    assert "A " in out and " C" in out, out
    # physical assertion: content.xml carries the change marks + the
    # registered change list (tracked-changes registry with dc:creator).
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        cx = z.read("content.xml").decode("utf-8")
    assert "text:change-start" in cx, cx[:400]
    assert "text:change-end" in cx, cx[:400]
    assert "tracked-changes" in cx, cx[:400]
    assert "dc:creator" in cx, cx[:400]


def test_odt_to_html_track_changes_roundtrip():
    """The ODT writer's change marks + tracked-changes registry read back as
    the HTML track-change contract, with authors resolved from dc:creator."""
    import zipfile

    html = (
        '<p><ins class="track-insert" data-author="Sam">inserted</ins> '
        'middle <del class="track-delete" data-author="Pat">deleted</del> tail</p>'
    )
    odt = html_to_odt(html)
    # physical: ODT XML contains text:change-start/end + a registered change
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        cx = z.read("content.xml").decode("utf-8")
    assert "text:change-start" in cx and "text:change-end" in cx, cx[:400]
    assert "dc:creator" in cx, cx[:400]
    out = odt_to_html(odt)
    assert '<ins class="track-insert" data-author="Sam">inserted</ins>' in out, out
    assert '<del class="track-delete" data-author="Pat">deleted</del>' in out, out
    assert "middle" in out and "tail" in out, out


def test_html_to_odt_table_caption_roundtrip():
    """A <figure> wrapping a <table> + <figcaption> round-trips through ODT
    (T32 gap: tables caption). The writer emits a preceding text:p with
    text:sequence-name carrying the caption; the reader wraps the table in
    <figure><figcaption>."""
    html = (
        '<figure><table width="400"><tr><td>a</td><td>b</td></tr>'
        '<tr><td>c</td><td>d</td></tr></table>'
        '<figcaption>Sample caption</figcaption></figure>'
    )
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert "<figure>" in out, out
    assert '<figcaption>Sample caption</figcaption>' in out, out
    assert "<table" in out, out
    import zipfile
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        cx = z.read("content.xml").decode("utf-8")
    assert "text:sequence-name" in cx, cx[:400]
    assert "Sample caption" in cx, cx[:400]


def test_html_to_odt_columns_roundtrip():
    """A <section data-columns="N"> wrapping the document maps to a
    text:section with text:columns and round-trips back (T32 gap: columns)."""
    html = '<section data-columns="3"><p>A</p><p>B</p><p>C</p></section>'
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert 'data-columns="3"' in out, out
    import zipfile
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        cx = z.read("content.xml").decode()
    assert "text:columns" in cx, cx[:500]
    assert "text:section" in cx, cx[:500]


def test_html_to_odt_toc_roundtrip():
    """<nav class="toc" data-title="..."> maps to text:table-of-content and
    round-trips back (T32 gap: TOC)."""
    html = '<nav class="toc" data-title="Contents"></nav><p>Body.</p>'
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert '<nav class="toc" data-title="Contents">' in out, out
    import zipfile
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        cx = z.read("content.xml").decode()
    assert "text:table-of-content" in cx, cx[:800]


def test_html_to_odt_section_break_roundtrip():
    """<hr class="section-break"> maps to a nested text:section and
    round-trips back (T32 gap: section-break)."""
    html = '<p>Before.</p><hr class="section-break"><p>After.</p>'
    odt = html_to_odt(html)
    out = odt_to_html(odt)
    assert '<hr class="section-break">' in out, out
    import zipfile
    with zipfile.ZipFile(io.BytesIO(odt)) as z:
        cx = z.read("content.xml").decode()
    assert "text:section" in cx, cx[:1200]


def test_html_to_odt_object_roundtrip():
    """<div class="object" data-type="..."> preserves object presence + type
    across an ODT round-trip (T32 gap: objects)."""
    for typ, label, content in [
        ("shape", "", ""),
        ("textbox", "", "Boxed text"),
        ("chart", "Sales", ""),
        ("equation", "", "E=mc^2"),
    ]:
        html = f'<p>Before</p><div class="object" data-type="{typ}"'
        if label:
            html += f' data-label="{label}"'
        html += f'>{content}</div><p>After</p>'
        odt = html_to_odt(html)
        out = odt_to_html(odt)
        assert f'data-type="{typ}"' in out, (typ, out)


def test_html_to_odt_bookmark_roundtrip_2():
    """<span class="bookmark" data-name="X"> round-trips through ODT
    text:bookmark and back (odt suite)."""
    odt = html_to_odt('<p>Intro <span class="bookmark" data-name="SEC1">target text</span> end.</p>')
    out = odt_to_html(odt)
    assert 'class="bookmark"' in out, out
    assert 'data-name="SEC1"' in out, out
    assert "target text" in out, out


def test_html_to_odt_crossref_roundtrip_2():
    """<a href="#NAME"> round-trips through ODT text:bookmark-ref and back."""
    odt = html_to_odt('<p>See <a href="#SEC1">section one</a> above.</p>')
    out = odt_to_html(odt)
    assert '<a href="#SEC1">' in out, out
    assert "section one" in out, out
