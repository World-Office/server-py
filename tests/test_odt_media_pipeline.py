"""ODT media pipeline internals: MIME sniffing, JPEG SOF parsing, data-URI
codec, and graceful degradation on corrupt packages.

Paradigm: **UNIT**. Complements test_odt_converter.py (which exercises the
public roundtrip through the odf package) by pinning the private helpers
directly: magic-byte tables, dimension parsing, and best-effort failure
modes. Deterministic: no network, no sleeps, no external tools.
"""

from __future__ import annotations

import io
import zipfile

from src.editor.odt_converter import (
    _data_uri,
    _decode_data_uri,
    _extract_pictures,
    _jpeg_dimensions,
    _sniff_mime,
)

# -----------------------------------------------------------------------------
# fixtures
# -----------------------------------------------------------------------------


def _png_bytes(w: int = 2, h: int = 3) -> bytes:
    import struct

    ihdr = (b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + ihdr


def _jpeg_bytes(w: int, h: int) -> bytes:
    """Minimal JPEG: SOI + SOF0 carrying dimensions + EOI."""
    sof0 = bytes([0xFF, 0xC0, 0x00, 0x0B, 0x08,
                  (h >> 8) & 0xFF, h & 0xFF,
                  (w >> 8) & 0xFF, w & 0xFF,
                  0x01, 0x11, 0x00])
    return b"\xff\xd8" + sof0 + b"\xff\xd9"


# -----------------------------------------------------------------------------
# _sniff_mime: magic bytes
# -----------------------------------------------------------------------------


def test_sniff_mime_all_supported_magics():
    assert _sniff_mime(_png_bytes()) == "image/png"
    assert _sniff_mime(b"GIF89a......") == "image/gif"
    assert _sniff_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert _sniff_mime(b"BM\x00\x00\x00\x00") == "image/bmp"
    assert _sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert _sniff_mime(b"<?xml version='1.0'?><svg/>") == "image/svg+xml"
    assert _sniff_mime(b"<svg xmlns='...'/>") == "image/svg+xml"


def test_sniff_mime_unknown_falls_back_to_png():
    """Unrecognized bytes do not raise; the fallback is image/png."""
    assert _sniff_mime(b"\x00\x01\x02\x03not-an-image") == "image/png"
    assert _sniff_mime(b"") == "image/png"


# -----------------------------------------------------------------------------
# _jpeg_dimensions: SOF marker walk
# -----------------------------------------------------------------------------


def test_jpeg_dimensions_reads_sof0():
    assert _jpeg_dimensions(_jpeg_bytes(640, 480)) == (640, 480)
    assert _jpeg_dimensions(_jpeg_bytes(1, 1)) == (1, 1)


def test_jpeg_dimensions_progressive_sof2_and_garbage():
    sof2 = bytes([0xFF, 0xC2, 0x00, 0x0B, 0x08, 0x02, 0x00, 0x03, 0x00,
                  0x01, 0x11, 0x00])
    data = b"\xff\xd8" + sof2 + b"\xff\xd9"
    assert _jpeg_dimensions(data) == (768, 512)
    # no SOF marker at all
    assert _jpeg_dimensions(b"\xff\xd8\xff\xd9") is None
    assert _jpeg_dimensions(b"not a jpeg") is None


# -----------------------------------------------------------------------------
# data URI codec
# -----------------------------------------------------------------------------


def test_data_uri_encode_decode_roundtrip():
    payload = b"\x89PNG-fake-bytes"
    uri = _data_uri("image/png", payload)
    assert uri.startswith("data:image/png;base64,")
    mime, decoded = _decode_data_uri(uri)
    assert mime == "image/png"
    assert decoded == payload


def test_decode_data_uri_rejects_remote_and_garbage():
    """http(s) sources and non-URIs are not embeddable -> (None, None)."""
    assert _decode_data_uri("https://example.com/x.png") == (None, None)
    assert _decode_data_uri("not a uri at all") == (None, None)
    assert _decode_data_uri("") == (None, None)


# -----------------------------------------------------------------------------
# _extract_pictures: package parsing + corruption resistance
# -----------------------------------------------------------------------------


def _odt_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_extract_pictures_maps_pictures_dir():
    png = _png_bytes()
    odt = _odt_zip({
        "mimetype": b"application/vnd.oasis.opendocument.text",
        "content.xml": b"<office:document-content/>",
        "Pictures/pic1.png": png,
        "Pictures/pic2.jpg": b"\xff\xd8\xff",
    })
    pics = _extract_pictures(odt)
    assert set(pics) == {"Pictures/pic1.png", "Pictures/pic2.jpg"}
    assert pics["Pictures/pic1.png"] == ("image/png", png)
    assert pics["Pictures/pic2.jpg"][0] == "image/jpeg"


def test_extract_pictures_on_corrupt_package_returns_empty():
    """Best-effort contract: a non-zip payload yields {} — no exception."""
    assert _extract_pictures(b"this is not a zip file") == {}
    assert _extract_pictures(b"") == {}
    # a zip without any Pictures/ member also maps to {}
    odt = _odt_zip({"content.xml": b"<x/>"})
    assert _extract_pictures(odt) == {}
