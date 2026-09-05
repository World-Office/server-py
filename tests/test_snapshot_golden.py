"""Golden-master (snapshot) tests for the wire contracts.

The golden-master school: instead of asserting a handful of hand-picked
fields, the *entire* canonical response is pinned byte-for-byte to a stored
golden file. Any intentional contract change (new field, renamed key,
renumbered op, changed XML) shows up as a noisy diff the author must
review — and regenerate deliberately.

Volatile, time-derived values are normalized away before comparison
(CheckFileInfo ``Version``/``LastModifiedTime``, the configured
``public_url`` in the discovery XML), so goldens are stable across runs.

Regenerate on purpose::

    UPDATE_GOLDEN=1 uv run pytest tests/test_snapshot_golden.py -q
"""

from __future__ import annotations

import difflib
import io
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Config
from src.editor.collab import reset_hub
from src.editor.router import router as editor_router
from src.editor.session import SessionRegistry
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.router import router as wopi_router

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


@pytest.fixture

def client(tmp_path):
    reset_hub()
    db = str(tmp_path / "t.db")
    content = str(tmp_path / "content")
    store = DocumentStore(db, content)
    cfg = Config(database=db, content_dir=content, jwt_secret="test-secret")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.sessions = SessionRegistry()
        app.state.config = cfg
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(wopi_router)
    app.include_router(editor_router)
    with TestClient(app) as c:
        c.test_store = store  # type: ignore[attr-defined]
        yield c
    wipe_db(db)
    wipe_dir(content)
    reset_hub()


def _docx_bytes(text: str = "Golden body") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _maybe_update(name: str, text: str) -> bool:
    """If UPDATE_GOLDEN is set, (re)write the golden file and report it."""
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        (GOLDEN_DIR / name).write_text(text)
        print(f"  [golden] {name} updated")
        return True
    return False


def _assert_golden(name: str, canonical: str) -> None:
    golden_path = GOLDEN_DIR / name
    assert golden_path.exists(), (
        f"golden file {golden_path} missing — generate with "
        f"UPDATE_GOLDEN=1 uv run pytest tests/test_snapshot_golden.py"
    )
    golden = golden_path.read_text()
    if canonical != golden:
        diff = "".join(
            difflib.unified_diff(
                golden.splitlines(keepends=True),
                canonical.splitlines(keepends=True),
                fromfile="golden",
                tofile="current",
            )
        )
        raise AssertionError(
            f"golden contract {name} drifted — review the diff; if intentional, "
            f"regenerate with UPDATE_GOLDEN=1\n{diff}"
        )


def test_check_file_info_golden(client):
    """The full CheckFileInfo JSON shape is pinned (modulo timestamps)."""
    store = client.test_store  # type: ignore[attr-defined]
    store.init("golden-doc", "hello.docx")
    store.put_content("golden-doc", _docx_bytes())
    res = client.get("/wopi/files/golden-doc")
    assert res.status_code == 200
    body = res.json()
    body.pop("Version", None)  # timestamp-derived
    body.pop("LastModifiedTime", None)  # timestamp-derived
    canonical = json.dumps(body, indent=2, sort_keys=True) + "\n"
    if _maybe_update("check_file_info.json", canonical):
        return
    _assert_golden("check_file_info.json", canonical)


def test_discovery_xml_golden(client):
    """The OpenCloud discovery XML is pinned (modulo the configured URL)."""
    res = client.get("/hosting/discovery")
    assert res.status_code == 200
    public_url = client.app.state.config.public_url  # type: ignore[attr-defined]
    canonical = res.text.replace(public_url, "{PUBLIC_URL}")
    if _maybe_update("discovery.xml", canonical):
        return
    _assert_golden("discovery.xml", canonical)


def test_collab_ops_wire_contract_golden(client):
    """The collab apply-ops reply (rev/applied/ops/text) is fully
    deterministic for a fixed op batch — pin the wire contract."""
    reset_hub()
    batch = json.dumps(
        {
            "client_id": "golden-client",
            "base_rev": 0,
            "ops": [
                {"t": "insert", "s": "site-1", "b": 1, "n": 5, "chars": "hello",
                 "originSite": "", "originSeq": 0},
                {"t": "insert", "s": "site-1", "b": 6, "n": 6, "chars": " world",
                 "originSite": "site-1", "originSeq": 5},
            ],
        }
    )
    res = client.post("/api/documents/golden-doc/collab/ops", content=batch)
    assert res.status_code == 200
    canonical = json.dumps(res.json(), indent=2, sort_keys=True) + "\n"
    reset_hub()
    if _maybe_update("collab_ops.json", canonical):
        return
    _assert_golden("collab_ops.json", canonical)
    reset_hub()
