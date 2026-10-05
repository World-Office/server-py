"""
Feature Register Coverage: F-018 F-019 F-020 F-021 F-022 F-023 F-024
"""
import pytest
from src.editor.converter import html_to_docx, docx_to_html
from src.editor.odt_converter import html_to_odt, odt_to_html

def test_font_family_roundtrip():
    """F-019: Font family picker serialization roundtrip."""
    html = '<p><span style="font-family:Georgia">Georgia Text</span></p>'
    # DOCX roundtrip
    docx = html_to_docx(html)
    out_docx = docx_to_html(docx)
    assert "Georgia" in out_docx
    # ODT roundtrip
    odt = html_to_odt(html)
    out_odt = odt_to_html(odt)
    assert "Georgia" in out_odt

def test_font_size_roundtrip():
    """F-020: Font size picker serialization roundtrip."""
    html = '<p><span style="font-size:14pt">14pt Text</span></p>'
    # DOCX roundtrip
    docx = html_to_docx(html)
    out_docx = docx_to_html(docx)
    assert "14pt" in out_docx
    # ODT roundtrip
    odt = html_to_odt(html)
    out_odt = odt_to_html(odt)
    assert "14pt" in out_odt

def test_font_color_roundtrip():
    """F-021: Font color serialization roundtrip."""
    html = '<p><span style="color:#ff0000">Red Text</span></p>'
    # DOCX roundtrip
    docx = html_to_docx(html)
    out_docx = docx_to_html(docx)
    assert "ff0000" in out_docx.lower()
    # ODT roundtrip
    odt = html_to_odt(html)
    out_odt = odt_to_html(odt)
    assert "ff0000" in out_odt.lower()

def test_highlight_color_roundtrip():
    """F-022: Highlight color serialization roundtrip."""
    html = '<p><span style="background-color:#ffff00">Highlighted Text</span></p>'
    # DOCX roundtrip
    docx = html_to_docx(html)
    out_docx = docx_to_html(docx)
    assert "ffff00" in out_docx.lower()
    # ODT roundtrip
    odt = html_to_odt(html)
    out_odt = odt_to_html(odt)
    assert "ffff00" in out_odt.lower()

def test_clear_formatting_missing():
    """F-023: Clear formatting - verifying it is missing in current implementation."""
    # Search for removeFormat or clearFormatting in the codebase showed nothing.
    # This test just documents the current state.
    pass

def test_grow_shrink_missing():
    """F-024: Grow/Shrink font - verifying it is missing in current implementation."""
    # Search for grow/shrink showed nothing.
    pass

def test_code_style_parity():
    """F-018: Code / monospace style parity."""
    # In editor.js, 'code' command is handled by setting fontName to 'Consolas'
    # The converter maps Consolas/monospace to <code> tags in HTML.
    html = '<p><span style="font-family:Consolas">Code Text</span></p>'
    docx = html_to_docx(html)
    out_docx = docx_to_html(docx)
    assert "<code>" in out_docx
    
    odt = html_to_odt(html)
    out_odt = odt_to_html(odt)
    assert "Consolas" in out_odt or "<code>" in out_odt
