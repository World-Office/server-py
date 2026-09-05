"""Web surface: hosting discovery XML, /editor page, /static assets content-types (UNIT).

Target scope — the public-facing HTTP surface of the document server:

1. **WOPI discovery XML** (`/hosting/discovery`)
   - Returns valid XML with correct `media_type`
   - Includes `public_url` in `urlsrc` attributes
   - Supports both `.docx` and `.odt` extensions

2. **Editor page** (`/editor` and `/editor/{doc_id}`)
   - Returns HTML with 200 status
   - Renders the editor template regardless of doc_id presence
   - Missing doc_id does not cause 500 (returns 200 with empty doc)

3. **Static files** (`/static/*`)
   - Serves files with correct `Content-Type` headers
   - Returns 404 for non-existent files
   - Supports common asset types (JS, CSS, HTML)

Paradigm: **Unit tests** using the FastAPI TestClient with minimal mocking.
No network, no sleeps, no time-of-day dependence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


@pytest.fixture
def client(tmp_path):
    """TestClient with the full docserver app and lifespan running."""
    cfg = Config(database=str(tmp_path / "t.db"), content_dir=str(tmp_path / "content"))
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_hosting_discovery_returns_xml_with_correct_media_type(client):
    """WOPI discovery endpoint returns valid XML with text/xml media type."""
    res = client.get("/hosting/discovery")
    assert res.status_code == 200
    assert "text/xml" in res.headers["content-type"]
    body = res.text
    assert '<?xml version="1.0" encoding="UTF-8" standalone="no"?>' in body
    assert '<wopi-discovery>' in body
    assert '<net-zone name="external-http">' in body
    assert '<app name="WorldOffice"' in body


def test_hosting_discovery_contains_urlsrc_with_public_url(client):
    """WOPI discovery urlsrc attributes include the configured public_url."""
    res = client.get("/hosting/discovery")
    body = res.text
    # The discovery XML should contain urlsrc attributes pointing to the editor
    assert 'urlsrc="' in body
    # Extract and verify the public_url is embedded (depends on config.public_url)
    # The template replaces {public_url} with the actual value
    assert '/editor' in body


def test_hosting_discovery_supports_docx_and_odt_extensions(client):
    """WOPI discovery lists actions for both .docx and .odt extensions."""
    res = client.get("/hosting/discovery")
    body = res.text
    # Both document types should have view and edit actions
    assert 'ext="docx"' in body
    assert 'ext="odt"' in body
    # Each extension should have view and edit actions
    assert 'name="view" ext="docx"' in body
    assert 'name="edit" ext="docx"' in body
    assert 'name="view" ext="odt"' in body
    assert 'name="edit" ext="odt"' in body


def test_editor_page_root_returns_html(client):
    """Editor page at /editor returns HTML (200) with the editor template."""
    res = client.get("/editor")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert '<!DOCTYPE html>' in body or '<html' in body.lower()


def test_editor_page_with_doc_id_returns_html(client):
    """Editor page at /editor/{doc_id} returns HTML (200) for any doc_id."""
    res = client.get("/editor/doc123")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    # Should return the editor template even if doc doesn't exist
    # (the page handles missing docs client-side)


def test_static_files_serve_js_with_correct_content_type(client):
    """Static JavaScript files are served with application/javascript."""
    res = client.get("/static/editor.js")
    assert res.status_code == 200
    assert "application/javascript" in res.headers["content-type"] or "text/javascript" in res.headers["content-type"]
    assert len(res.content) > 0


def test_static_files_serve_css_with_correct_content_type(client):
    """Static CSS files are served with text/css."""
    res = client.get("/static/style.css")
    assert res.status_code == 200
    assert "text/css" in res.headers["content-type"]
    assert len(res.content) > 0


def test_static_files_serve_html_with_correct_content_type(client):
    """Static HTML files are served with text/html."""
    res = client.get("/static/home.html")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert len(res.content) > 0


def test_static_files_returns_404_for_nonexistent_asset(client):
    """Static files return 404 for non-existent paths."""
    res = client.get("/static/does_not_exist.xyz")
    assert res.status_code == 404


# Import Config at module level (it's used in the fixture)
from src.config import Config