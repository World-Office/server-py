"""WOPI protocol-level tests against the live docserver (no browser).

feature register: F-001 F-124 (harness-graph)
"""

import pytest
import requests

from conftest import EDITOR_BASE

import urllib3

urllib3.disable_warnings()


@pytest.mark.wopi
def test_health_endpoint():
    r = requests.get(f"{EDITOR_BASE}/health", timeout=15, verify=False)
    assert r.status_code == 200
    assert "ok" in r.text.lower() or r.text.strip() in ("OK", "ok", " healthy")


@pytest.mark.wopi
def test_discovery_advertises_word_editor():
    r = requests.get(f"{EDITOR_BASE}/hosting/discovery", timeout=15, verify=False)
    assert r.status_code == 200
    xml = r.text
    assert "World Office" in xml or "word" in xml.lower()
    # docx and odt edit actions must carry a urlsrc for the browser flow
    assert "urlsrc" in xml, "discovery actions lack urlsrc"


@pytest.mark.wopi
def test_discovery_covers_office_extensions():
    r = requests.get(f"{EDITOR_BASE}/hosting/discovery", timeout=15, verify=False)
    xml = r.text.lower()
    for ext in ("docx", "odt"):
        assert ext in xml, f"no discovery entry for .{ext}"


@pytest.mark.wopi
def test_check_file_info_rejects_bogus_token():
    """A tampered access token must never yield file metadata."""
    r = requests.get(
        f"{EDITOR_BASE}/wopi/files/somefile?access_token=bogus.token",
        timeout=15,
        verify=False,
    )
    assert r.status_code != 200, "bogus token must not authenticate CheckFileInfo"


@pytest.mark.wopi
def test_put_file_rejects_bogus_token():
    r = requests.post(
        f"{EDITOR_BASE}/wopi/files/somefile/contents?access_token=bogus.token",
        data=b"x",
        timeout=15,
        verify=False,
    )
    assert r.status_code != 200, "bogus token must not be able to overwrite files"


@pytest.mark.wopi
def test_editor_ui_bundle_served():
    r = requests.get(f"{EDITOR_BASE}/word/", timeout=15, verify=False)
    assert r.status_code == 200
    assert "assets/index-" in r.text, "word editor index.html lacks the JS bundle"
