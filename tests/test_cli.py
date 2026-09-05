"""Unit tests for docserver CLI — arg parsing, dispatch, exit codes.

Paradigm: **Unit tests** for the CLI surface in ``src.cli``. Coverage:

1. **Dispatch** — ``main()`` routes each subcommand (seed, list, health) to
   its handler with the parsed arguments.
2. **Exit codes** — argparse failures (no subcommand, unknown subcommand,
   missing required argument) exit with argparse's status ``2``; runtime
   failures (unreachable server, invalid DOCX) exit with a non-zero message.
3. **Command behaviour** — ``cmd_list`` / ``cmd_health`` / ``cmd_seed``
   rendered output and store interactions.

Everything is deterministic: network egress (``urllib.request.urlopen``),
the SQLite-backed store (``_store_from_config``) and the optional ``docx``
dependency are all mocked. No sleeps, no time-of-day dependence.
"""

from __future__ import annotations

import argparse
import sys
import types
import zipfile

import pytest

from src import cli

# ---------------------------------------------------------------------------
# Dispatch — main() routes to the correct handler
# ---------------------------------------------------------------------------


def test_main_dispatches_list_to_cmd_list(capfd, monkeypatch):
    """``main(["list"])`` calls the store-backed list handler."""
    class MockStore:
        def list(self):
            return []

    monkeypatch.setattr(cli, "_store_from_config", lambda: MockStore())

    cli.main(["list"])
    out = capfd.readouterr().out
    assert "no documents" in out


def test_main_dispatches_seed_with_parsed_args(monkeypatch, tmp_path):
    """``main(["seed", path, "--doc-id", ...])`` routes to cmd_seed and
    hands it the parsed ``--doc-id`` plus an open file for ``path``."""
    docx_path = tmp_path / "sample.docx"
    docx_path.write_bytes(b"PK\x03\x04 dummy")

    calls: list[argparse.Namespace] = []

    def fake_cmd_seed(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli, "cmd_seed", fake_cmd_seed)

    cli.main(["seed", str(docx_path), "--doc-id", "abc-123"])

    assert len(calls) == 1
    args = calls[0]
    assert args.command == "seed"
    assert args.doc_id == "abc-123"
    # argparse FileType("rb") hands the handler an open binary file object
    assert args.path.name.endswith("sample.docx")
    # NOTE: existing behaviour — FileType returns a file object, but cmd_seed
    # calls path.read_bytes()/path.name like a pathlib.Path. Pinned as-is.
    assert isinstance(args.path, type(open(str(docx_path), "rb")))


# ---------------------------------------------------------------------------
# Exit codes — argparse errors
# ---------------------------------------------------------------------------


def test_main_no_subcommand_exits_2(capfd):
    """No subcommand is an argparse error (exit status 2)."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code == 2


def test_main_unknown_subcommand_exits_2(capfd):
    """Unknown subcommand is an argparse error (exit status 2)."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["restart"])
    assert exc_info.value.code == 2


def test_main_seed_missing_path_exits_2(capfd):
    """``seed`` without its required path argument exits 2."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["seed"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# cmd_list — store rendering
# ---------------------------------------------------------------------------


def test_cmd_list_empty_store_prints_placeholder(capfd, monkeypatch):
    """A store with no documents prints ``no documents``."""
    class MockStore:
        def list(self):
            return []

    monkeypatch.setattr(cli, "_store_from_config", lambda: MockStore())

    cli.cmd_list(argparse.Namespace())
    assert "no documents" in capfd.readouterr().out


def test_cmd_list_renders_rows_and_lock_marker(capfd, monkeypatch):
    """Rows show id, size in bytes, and a lock marker for locked docs."""
    class MockStore:
        def list(self):
            return [
                {"id": "doc1", "size": 1024, "lock_token": ""},
                {"id": "doc2", "size": 2048, "lock_token": "tok-9"},
            ]

    monkeypatch.setattr(cli, "_store_from_config", lambda: MockStore())

    cli.cmd_list(argparse.Namespace())
    out = capfd.readouterr().out

    assert "doc1" in out and "1024 B" in out
    assert "doc2" in out and "2048 B" in out
    assert "[locked]" in out
    # The CLI prints rows in the order the store returns them (ordering is
    # the store's job); each doc appears on its own line.
    assert sum(1 for line in out.splitlines() if line.strip()) == 2


# ---------------------------------------------------------------------------
# cmd_health — reachability
# ---------------------------------------------------------------------------


def test_cmd_health_prints_json_when_reachable(capfd, monkeypatch):
    """A reachable server makes cmd_health print the JSON payload."""
    class MockResponse:
        def __init__(self):
            self._payload = b'{"status": "ok", "version": "1.0"}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._payload

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: MockResponse()
    )

    cli.cmd_health(argparse.Namespace())
    out = capfd.readouterr().out
    assert '"status": "ok"' in out
    assert '"version": "1.0"' in out


def test_cmd_health_unreachable_server_exits_nonzero(capfd, monkeypatch):
    """An unreachable server exits non-zero with a diagnostic message."""
    def unreachable(*a, **k):
        raise OSError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", unreachable)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["health"])
    code = exc_info.value.code
    assert code != 0
    assert "error: server not reachable" in code
    assert "Connection refused" in code


# ---------------------------------------------------------------------------
# cmd_seed — DOCX registration
# ---------------------------------------------------------------------------


def test_cmd_seed_rejects_non_docx_and_exits(capfd, monkeypatch, tmp_path):
    """An unreadable DOCX makes cmd_seed exit with an error message."""
    bad = tmp_path / "notes.txt"
    bad.write_text("definitely not a docx")

    mock_docx = types.ModuleType("docx")

    class RejectingDoc:
        def __init__(self, *a, **k):
            raise Exception("package corruption detected")

    mock_docx.Document = RejectingDoc
    monkeypatch.setitem(sys.modules, "docx", mock_docx)
    monkeypatch.setattr(cli, "_store_from_config", lambda: object())

    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_seed(argparse.Namespace(path=bad, doc_id=""))
    code = exc_info.value.code
    assert code != 0
    assert "not a readable DOCX" in code
    assert "package corruption" in code


def test_cmd_seed_registers_valid_docx(capfd, monkeypatch, tmp_path):
    """A valid DOCX is registered under --doc-id (or filename) and stored."""
    docx_path = tmp_path / "sample.docx"
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")

    mock_docx = types.ModuleType("docx")
    mock_docx.Document = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "docx", mock_docx)

    seen: dict = {}

    class RecordingStore:
        def init(self, doc_id, name):
            seen["init"] = (doc_id, name)

        def put_content(self, doc_id, data):
            seen["content"] = (doc_id, data)

    monkeypatch.setattr(cli, "_store_from_config", lambda: RecordingStore())

    cli.cmd_seed(argparse.Namespace(path=docx_path, doc_id="custom-id"))

    assert seen["init"] == ("custom-id", "sample.docx")
    assert seen["content"] == ("custom-id", docx_path.read_bytes())
    assert "seeded custom-id" in capfd.readouterr().out


def test_cmd_seed_uses_filename_when_no_doc_id(capfd, monkeypatch, tmp_path):
    """Without ``--doc-id``, cmd_seed falls back to the file name."""
    docx_path = tmp_path / "report.docx"
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")

    mock_docx = types.ModuleType("docx")
    mock_docx.Document = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "docx", mock_docx)

    seen: dict = {}

    class RecordingStore:
        def init(self, doc_id, name):
            seen["init"] = (doc_id, name)

        def put_content(self, doc_id, data):
            seen["content"] = (doc_id, data)

    monkeypatch.setattr(cli, "_store_from_config", lambda: RecordingStore())

    cli.cmd_seed(argparse.Namespace(path=docx_path, doc_id=""))

    assert seen["init"] == ("report.docx", "report.docx")
    assert seen["content"] == ("report.docx", docx_path.read_bytes())
