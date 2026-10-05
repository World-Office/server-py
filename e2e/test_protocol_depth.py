"""Protocol robustness: hard negatives and concurrency for the live docserver."""

import concurrent.futures

import pytest
import requests

from conftest import EDITOR_BASE

import urllib3

urllib3.disable_warnings()


@pytest.mark.wopi
def test_check_file_info_without_token_rejected():
    r = requests.get(f"{EDITOR_BASE}/wopi/files/x", timeout=15, verify=False)
    assert r.status_code in (400, 401, 403, 404), (
        f"missing token must not authenticate: {r.status_code}"
    )
    assert "access_token" not in (r.text or "")[:200] or r.status_code != 200


@pytest.mark.wopi
def test_put_file_without_token_rejected():
    r = requests.post(
        f"{EDITOR_BASE}/wopi/files/x/contents", data=b"evil", timeout=15, verify=False
    )
    assert r.status_code in (400, 401, 403, 404), (
        f"unauthenticated PutFile must fail: {r.status_code}"
    )


@pytest.mark.wopi
def test_unknown_route_is_404_not_500():
    r = requests.get(f"{EDITOR_BASE}/definitely/not/a/route", timeout=15, verify=False)
    assert r.status_code in (404, 405), f"unknown route returned {r.status_code}"


@pytest.mark.wopi
def test_discovery_concurrent_requests_ok():
    def hit(_):
        return requests.get(f"{EDITOR_BASE}/hosting/discovery", timeout=20, verify=False).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        codes = list(ex.map(hit, range(5)))
    assert all(c == 200 for c in codes), f"concurrent discovery failed: {codes}"


@pytest.mark.wopi
def test_editor_static_assets_served():
    idx = requests.get(f"{EDITOR_BASE}/editor", timeout=15, verify=False)
    assert idx.status_code == 200
    import re

    # \b so `.json` is not matched as a `.js` prefix (manifest.json!)
    assets = re.findall(r'/static/[\w.-]+\.js\b', idx.text)
    assert assets, "no JS bundle referenced"
    r = requests.get(f"{EDITOR_BASE}{assets[0]}", timeout=15, verify=False)
    assert r.status_code == 200
    assert len(r.content) > 10_000, "JS bundle suspiciously small"


@pytest.mark.wopi
def test_discovery_content_type_is_xml():
    r = requests.get(f"{EDITOR_BASE}/hosting/discovery", timeout=15, verify=False)
    ct = r.headers.get("content-type", "")
    assert "xml" in ct.lower() or r.text.lstrip().startswith("<?xml"), (
        f"discovery content-type: {ct}"
    )
