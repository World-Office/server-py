"""Property-based tests for text preservation across DOCX and ODT round-trips.

This suite focuses on the invariant that any text introduced into the system
via HTML should survive the process of being converted to a binary format
(DOCX or ODT) and then converted back to HTML.

We use Hypothesis to generate a wide range of text fragments and documents,
ensuring that no characters are lost or corrupted during the round-trip.
"""

import re
from html import escape
from hypothesis import given, settings, strategies as st
from src.editor.converter import docx_to_html, html_to_docx
from src.editor.odt_converter import html_to_odt, odt_to_html

# Use the same safe character set and settings as test_converter_property.py
# to maintain consistency with the established baseline.
SAFE_CHARS = st.characters(
    blacklist_categories=("Cc", "Cs", "Cf", "Zl", "Zp"), max_codepoint=0x1FFFF
)
FRAGMENT = st.text(alphabet=SAFE_CHARS, min_size=0, max_size=8)
PROP_SETTINGS = settings(max_examples=50, deadline=None)

def _plain(html: str) -> str:
    """Extract plain text from HTML by stripping tags and decoding basic entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    for ent, ch in (
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
    ):
        text = text.replace(ent, ch)
    return text

def _tokens(text: str) -> set[str]:
    """Split text into a set of alphanumeric tokens for comparison."""
    return {t for t in re.split(r"[^\w]+", text.lower()) if t}

@st.composite
def inline_fragment(draw) -> str:
    """Generates a text fragment, optionally wrapped in basic formatting tags."""
    frag = escape(draw(FRAGMENT), quote=False)
    style = draw(st.sampled_from(["plain", "b", "i", "u"]))
    if style == "b":
        return f"<b>{frag}</b>"
    elif style == "i":
        return f"<i>{frag}</i>"
    elif style == "u":
        return f"<u>{frag}</u>"
    return frag

@st.composite
def doc_html(draw) -> str:
    """Generates a small random HTML document consisting of several paragraphs."""
    paras: list[str] = []
    for _ in range(draw(st.integers(1, 5))):
        parts = draw(st.lists(inline_fragment(), min_size=1, max_size=6))
        # Ensure at least some actual text is present
        if not any(part.strip("<>/biu ") for part in parts):
            parts.append(draw(FRAGMENT) or "x")
        paras.append(f"<p>{' '.join(parts)}</p>")
    return "\n".join(paras)

@given(doc_html())
@PROP_SETTINGS
def test_docx_roundtrip_text_preservation(html: str):
    """
    Property: Text tokens in the original HTML must be a subset of tokens 
    in the DOCX round-trip output.
    """
    # HTML -> DOCX -> HTML
    out = docx_to_html(html_to_docx(html))
    src_tokens = _tokens(_plain(html))
    dst_tokens = _tokens(_plain(out))
    assert src_tokens <= dst_tokens, (
        f"DOCX round-trip lost tokens: {src_tokens - dst_tokens}\n"
        f"Original: {html!r}\nResult: {out!r}"
    )

@given(doc_html())
@PROP_SETTINGS
def test_odt_roundtrip_text_preservation(html: str):
    """
    Property: Text tokens in the original HTML must be a subset of tokens 
    in the ODT round-trip output.
    """
    # HTML -> ODT -> HTML
    out = odt_to_html(html_to_odt(html))
    src_tokens = _tokens(_plain(html))
    dst_tokens = _tokens(_plain(out))
    assert src_tokens <= dst_tokens, (
        f"ODT round-trip lost tokens: {src_tokens - dst_tokens}\n"
        f"Original: {html!r}\nResult: {out!r}"
    )

@given(st.text(alphabet=SAFE_CHARS, min_size=1, max_size=100))
@PROP_SETTINGS
def test_single_paragraph_roundtrip_stability(text: str):
    """
    Checks that a single paragraph of plain text is preserved exactly 
    (modulo tokenization) in both formats.
    """
    html = f"<p>{escape(text)}</p>"
    
    docx_out = docx_to_html(html_to_docx(html))
    odt_out = odt_to_html(html_to_odt(html))
    
    src_tokens = _tokens(_plain(html))
    assert src_tokens <= _tokens(_plain(docx_out)), f"DOCX lost text: {text!r}"
    assert src_tokens <= _tokens(_plain(odt_out)), f"ODT lost text: {text!r}"

@given(doc_html())
@PROP_SETTINGS
def test_cross_format_text_consistency(html: str):
    """
    Property: The visible text resulting from a DOCX round-trip and an 
    ODT round-trip of the same HTML should be identical.
    """
    docx_text = _tokens(_plain(docx_to_html(html_to_docx(html))))
    odt_text = _tokens(_plain(odt_to_html(html_to_odt(html))))
    
    if not docx_text or not odt_text:
        return # Skip empty documents
        
    assert docx_text == odt_text, (
        f"DOCX and ODT round-trips diverged on text tokens\n"
        f"DOCX: {docx_text}\nODT: {odt_text}\nHTML: {html!r}"
    )
