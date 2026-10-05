"""Format-spec conformance for engine-produced DOCX and ODT artifacts
(F-002 Download as ODT, F-003 Download as DOCX).

The converters are validated against the actual format specifications —
not just "the zip opens":

* OOXML: ECMA-376 Part 2 (Open Packaging Conventions) — [Content_Types].xml
  at the package root, relationship parts, entry-name and uniqueness rules —
  and ECMA-376 Part 1 (WordprocessingML): w:document/w:body structure in the
  proper namespace. (OOXML is ECMA/ISO-29500; there is no IETF RFC for it.)
* ODT: OASIS OpenDocument 1.2 (ISO/IEC 26300) packaging rules — the
  ``mimetype`` entry MUST be first, uncompressed (stored), and contain
  exactly the media type; META-INF/manifest.xml must declare the root and
  content/styles entries; content.xml/styles.xml must carry the ODF
  namespaces and office:text body.

Structure of the validators: ``validate_docx_package`` and
``validate_odt_package`` raise ``AssertionError`` with the spec clause on
violation. Each has a negative control proving the validator actually
bites (a broken package must be rejected — loud failure, no silent pass).
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import pytest

from src.editor.converter import docx_to_html, html_to_docx
from src.editor.odt_converter import html_to_odt, odt_to_html

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
RELS_CT = "application/vnd.openxmlformats-package.relationships+xml"
OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
ODT_MIME = "application/vnd.oasis.opendocument.text"
WML_MAIN_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"

SAMPLE_HTML = (
    "<h1>Spec Conformance Title</h1>"
    "<p>First paragraph with <strong>bold</strong> text.</p>"
    "<p>Second paragraph.</p>"
)


def _parse(data: bytes) -> zipfile.ZipFile:
    zf = zipfile.ZipFile(io.BytesIO(data))
    bad = zf.testzip()
    assert bad is None, f"corrupt zip entry: {bad}"
    return zf


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(zf.read(name))
    except ET.ParseError as e:
        raise AssertionError(f"{name} is not well-formed XML: {e}") from e


# ---------------------------------------------------------------------------
# ECMA-376 Part 2: Open Packaging Conventions (the DOCX container)
# ---------------------------------------------------------------------------

def validate_docx_package(data: bytes) -> None:
    """Assert OPC package rules; raises AssertionError with the clause."""
    zf = _parse(data)
    names = zf.namelist()

    assert "[Content_Types].xml" in names, (
        "OPC (ECMA-376-2 §10.1.2.1): [Content_Types].xml must exist at the package root"
    )
    # OPC §10.1.2.3 / physical-model naming: no leading slash, no dot-dot
    for n in names:
        assert not n.startswith(("/", "\\")), f"OPC: entry name must be relative: {n!r}"
        assert ".." not in n.split("/"), f"OPC: entry name must not traverse up: {n!r}"
    assert len(names) == len(set(names)), "OPC: duplicate entry names are forbidden"

    ct = _xml(zf, "[Content_Types].xml")
    tag = lambda t: f"{{http://schemas.openxmlformats.org/package/2006/content-types}}{t}"
    defaults = {e.get("Extension", "").lower(): e.get("ContentType") for e in ct.iter(tag("Default"))}
    assert defaults.get("rels") == RELS_CT, (
        "OPC §10.1.2.4: Default rels -> package relationships content type"
    )
    assert defaults.get("xml") == "application/xml", "OPC: Default xml -> application/xml"
    overrides = {o.get("PartName"): o.get("ContentType") for o in ct.iter(tag("Override"))}
    assert overrides.get("/word/document.xml") == WML_MAIN_CT, (
        "ECMA-376-1 §11.3.6: /word/document.xml Override must be wordprocessingml.document.main+xml"
    )

    rels = _xml(zf, "_rels/.rels")
    rel_tag = f"{{{RELS_NS}}}Relationship"
    office_rels = [
        r for r in rels.iter(rel_tag)
        if r.get("Type", "").endswith("/officeDocument") and (r.get("Target") or "").startswith("word/")
    ]
    assert office_rels, (
        "OPC §10.2: package rels must point at the main document via the officeDocument relationship"
    )

    assert "word/_rels/document.xml.rels" in names, "OPC: document part must ship its rels part"


def validate_wordprocessingml(data: bytes) -> None:
    """Assert ECMA-376-1 WordprocessingML structure of word/document.xml."""
    zf = _parse(data)
    doc = _xml(zf, "word/document.xml")
    assert doc.tag == f"{{{W_NS}}}document", (
        f"ECMA-376-1 §11.3.6: root must be w:document in {W_NS}, got {doc.tag}"
    )
    bodies = doc.findall(f"{{{W_NS}}}body")
    assert len(bodies) == 1, "ECMA-376-1 §11.3.6: exactly one w:body"
    paragraphs = bodies[0].findall(f"{{{W_NS}}}p")
    assert paragraphs, "ECMA-376-1 §17.3.1.22: body must contain paragraphs"


# ---------------------------------------------------------------------------
# OASIS OpenDocument 1.2 (ISO/IEC 26300): the ODT container
# ---------------------------------------------------------------------------

def validate_odt_package(data: bytes) -> None:
    """Assert ODF 1.2 packaging rules; raises AssertionError with the clause."""
    zf = _parse(data)
    infos = zf.infolist()

    assert infos, "ODF: package must not be empty"
    first = infos[0]
    assert first.filename == "mimetype", (
        "ODF 1.2 §3.3: the first entry must be 'mimetype' "
        f"(got {first.filename!r})"
    )
    assert first.compress_type == zipfile.ZIP_STORED, (
        "ODF 1.2 §3.3: the mimetype entry must be STORED (uncompressed)"
    )
    assert zf.read("mimetype") == ODT_MIME.encode(), (
        f"ODF 1.2 §3.3: mimetype content must be exactly {ODT_MIME!r}"
    )

    manifest = _xml(zf, "META-INF/manifest.xml")
    assert manifest.tag == f"{{{MANIFEST_NS}}}manifest", "ODF §3.4: manifest root namespace"
    # ODF 1.2 §3.4: manifest-version is OPTIONAL (mandatory only in ODF 1.3);
    # when present it must declare 1.2
    mv = manifest.get("manifest-version") or manifest.get(f"{{{MANIFEST_NS}}}version")
    assert mv in (None, "1.2"), f"ODF §3.4: manifest-version must be 1.2, got {mv!r}"
    entry_tag = f"{{{MANIFEST_NS}}}file-entry"
    entries = {e.get(f"{{{MANIFEST_NS}}}full-path"): e.get(f"{{{MANIFEST_NS}}}media-type")
               for e in manifest.iter(entry_tag)}
    assert entries.get("/") == ODT_MIME, "ODF §3.4: root file-entry '/' must carry the text media type"
    assert entries.get("content.xml") == "text/xml", "ODF §3.4: content.xml entry (text/xml)"
    assert entries.get("styles.xml") == "text/xml", "ODF §3.4: styles.xml entry (text/xml)"


def validate_opendocument_content(data: bytes) -> None:
    """Assert ODF 1.2 content.xml / styles.xml structure."""
    zf = _parse(data)
    content = _xml(zf, "content.xml")
    assert content.tag == f"{{{OFFICE_NS}}}document-content", (
        f"ODF §2.2: content.xml root must be office:document-content, got {content.tag}"
    )
    body = content.find(f"{{{OFFICE_NS}}}body")
    assert body is not None, "ODF §2.2.1: office:body required"
    assert body.find(f"{{{OFFICE_NS}}}text") is not None, "ODF §2.2.1: office:text body for .odt"

    styles = _xml(zf, "styles.xml")
    assert styles.tag == f"{{{OFFICE_NS}}}document-styles", (
        "ODF §2.2: styles.xml root must be office:document-styles"
    )


# ---------------------------------------------------------------------------
# positive conformance: engine-produced artifacts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("html", [
    SAMPLE_HTML,
    "<p>plain only</p>",
    "<h2>Head</h2><ul><li>one</li><li>two</li></ul><p>after list</p>",
])
def test_docx_output_conforms_to_ecma376(html):
    """html_to_docx must produce an OPC + WordprocessingML valid package."""
    data = html_to_docx(html)
    validate_docx_package(data)
    validate_wordprocessingml(data)


@pytest.mark.parametrize("html", [
    SAMPLE_HTML,
    "<p>plain only</p>",
    "<h2>Head</h2><ul><li>one</li><li>two</li></ul><p>after list</p>",
])
def test_odt_output_conforms_to_odf12(html):
    """html_to_odt must produce an ODF 1.2 valid package."""
    data = html_to_odt(html)
    validate_odt_package(data)
    validate_opendocument_content(data)


def test_docx_all_xml_parts_well_formed():
    """Every .xml/.rels part in the produced package must parse."""
    zf = _parse(html_to_docx(SAMPLE_HTML))
    for n in zf.namelist():
        if n.endswith((".xml", ".rels")):
            _xml(zf, n)


def test_odt_all_xml_parts_well_formed():
    """Every .xml part in the produced package must parse."""
    zf = _parse(html_to_odt(SAMPLE_HTML))
    for n in zf.namelist():
        if n.endswith(".xml"):
            _xml(zf, n)


def test_seed_text_survives_into_spec_valid_output():
    """Semantic conformance: authored text is present in both formats."""
    docx = html_to_docx(SAMPLE_HTML)
    zf = zipfile.ZipFile(io.BytesIO(docx))
    xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    assert "Spec Conformance Title" in xml

    odt = html_to_odt(SAMPLE_HTML)
    zf2 = zipfile.ZipFile(io.BytesIO(odt))
    cxml = zf2.read("content.xml").decode("utf-8", "ignore")
    assert "Spec Conformance Title" in cxml


# ---------------------------------------------------------------------------
# negative controls: the validators must actually bite
# ---------------------------------------------------------------------------

def _rewrite_zip(source: bytes, transform) -> bytes:
    zf = zipfile.ZipFile(io.BytesIO(source))
    entries = [(i.filename, zf.read(i.filename), i.compress_type) for i in zf.infolist()]
    entries = transform(entries)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob, ctype in entries:
            out.writestr(zipfile.ZipInfo(name), blob, compress_type=ctype)
    return buf.getvalue()


def test_docx_validator_rejects_package_without_content_types():
    """Negative control: dropping [Content_Types].xml must fail validation."""
    broken = _rewrite_zip(
        html_to_docx(SAMPLE_HTML),
        lambda es: [(n, b, c) for n, b, c in es if n != "[Content_Types].xml"],
    )
    with pytest.raises(AssertionError, match="Content_Types"):
        validate_docx_package(broken)


def test_odt_validator_rejects_deflated_mimetype():
    """Negative control: mimetype DEFLATED (not first) must fail ODF §3.3."""
    entries = [
        ("content.xml", b"<x/>", zipfile.ZIP_DEFLATED),
        ("mimetype", ODT_MIME.encode(), zipfile.ZIP_DEFLATED),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name, blob, ctype in entries:
            out.writestr(name, blob, compress_type=ctype)
    with pytest.raises(AssertionError):
        validate_odt_package(buf.getvalue())


def test_odt_validator_rejects_wrong_mimetype_content():
    """Negative control: a docx media type inside .odt packaging must fail."""
    entries = [
        ("mimetype", b"application/vnd.openxmlformats-officedocument.wordprocessingml.document", zipfile.ZIP_STORED),
        ("META-INF/manifest.xml", b"<manifest/>", zipfile.ZIP_DEFLATED),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name, blob, ctype in entries:
            out.writestr(name, blob, compress_type=ctype)
    with pytest.raises(AssertionError):
        validate_odt_package(buf.getvalue())


# ---------------------------------------------------------------------------
# parser conformance: our parsers accept our own spec-valid output
# ---------------------------------------------------------------------------

def test_parsers_roundtrip_spec_valid_documents():
    """docx_to_html/odt_to_html must consume the artifacts we produce."""
    html = docx_to_html(html_to_docx(SAMPLE_HTML))
    assert "Spec Conformance Title" in html

    html2 = odt_to_html(html_to_odt(SAMPLE_HTML))
    assert "Spec Conformance Title" in html2
