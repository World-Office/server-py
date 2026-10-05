"""Egress guard (E17S5) + local-model E2E (E19S2).

The server's agent path makes ZERO network egress by construction: adapters
run client-side over injected transports, and the tool surface talks only to
the in-process store/hub. Two guards pin that:

1. a static scan — no HTTP/socket client may appear in ``src/ai``;
2. a runtime E2E — with ``socket.socket`` rigged to raise, a "local
   provider" model drives a real edit to completion.
"""

from __future__ import annotations

import io
import pathlib

import pytest

SRC_AI = pathlib.Path(__file__).resolve().parents[1] / "src" / "ai"

BANNED_MODULES = ("httpx", "requests", "urllib.request", "aiohttp", "socket")


def test_src_ai_has_no_network_clients():
    """Static containment: the whole agent module imports no network stack."""
    offenders = []
    for py in sorted(SRC_AI.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for mod in BANNED_MODULES:
                if (
                    f"import {mod}" in line
                    or f"from {mod} " in line
                    or f"from {mod}." in line
                ):
                    offenders.append(f"{py.name}:{line_no}: {stripped}")
    assert offenders == []


def test_agent_edits_complete_with_egress_disabled(monkeypatch, tmp_path):
    """E19S2: a fully local model edits a document — with the network
    stack disabled at the socket level, the run still completes."""
    from docx import Document

    from src.ai.runner import AgentRunner
    from src.ai.tools import ToolContext
    from src.editor.collab import CollabHub
    from src.lib.store import DocumentStore, wipe_db, wipe_dir

    def _no_socket(*args, **kwargs):
        raise AssertionError("network egress attempted — the agent path must be local-only")

    monkeypatch.setattr("socket.socket", _no_socket)
    monkeypatch.setattr("socket.create_connection", _no_socket)

    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "local.docx")
    doc = Document()
    doc.add_paragraph("on-prem seed")
    buf = io.BytesIO()
    doc.save(buf)
    store.put_content("doc1", buf.getvalue())

    class LocalModel:
        """Pretends to be an on-box model: pure function, zero I/O."""

        def __init__(self):
            self.turns = 0

        def __call__(self, messages):
            self.turns += 1
            if self.turns == 1:
                return [{"name": "get_context", "arguments": {"doc_id": "doc1"}}]
            if self.turns == 2:
                return [{"name": "apply_ops", "arguments": {
                    "doc_id": "doc1", "client_id": "agent=local",
                    "ops": [{"t": "ins", "at": 12, "text": " (local model)"}],
                }}]
            return []  # done

    report = AgentRunner(LocalModel()).run(
        ToolContext(store=store, hub=CollabHub()),
        "doc1", "agent=local", "ground, then edit", audit=store)

    assert report.stopped_reason == "done"
    assert report.ops_applied == 1
    assert "on-prem seed (local model)" in report.text
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")
