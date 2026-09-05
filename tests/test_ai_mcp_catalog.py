"""MCP tool catalog discovery and schema completeness.

UNIT+GOLD tests for TC-E13-01: verifies that the MCP server exposes
five tools with complete, model-agnostic schemas through both the
``tools/list`` RPC endpoint and the ``build_context`` Wire-to-ToolContext
path. Golden files pin the full catalog shape; unit tests assert
count, names, and schema properties.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

import pytest

from src.ai.mcp import McpServer, build_context
from src.ai.schemas import TOOL_CATALOG, TOOL_CATALOG_VERSION, TOOL_NAMES
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


# ----------------------------------------------------------------------
# Conftest-reusable server fixture (mirrors test_ai_mcp.py style)
# ----------------------------------------------------------------------


@pytest.fixture
def server(tmp_path):
    """McpServer wired to an empty store — pure protocol, no I/O."""
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("catalog-doc", "catalog.docx")
    ctx = ToolContext(store=store, hub=CollabHub())
    yield McpServer(ctx)
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ----------------------------------------------------------------------
# Unit tests: catalog structure and count
# ----------------------------------------------------------------------


def test_tool_catalog_contains_exactly_five_tools():
    """The advertised catalog has exactly five tool schemas."""
    assert len(TOOL_CATALOG) == 5
    assert len(TOOL_NAMES) == 5


def test_tool_catalog_names_match_expected_set():
    """The five tools are read_doc, apply_ops, get_versions, lock, presence."""
    expected = {"read_doc", "apply_ops", "get_versions", "lock", "presence"}
    assert set(TOOL_NAMES) == expected


def test_each_tool_has_name_description_and_input_schema():
    """Every tool in the catalog provides name, description, and inputSchema."""
    for tool in TOOL_CATALOG:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert isinstance(tool["name"], str)
        assert isinstance(tool["description"], str)
        assert isinstance(tool["inputSchema"], dict)


def test_each_input_schema_is_object_with_properties():
    """Each tool's inputSchema is a JSON Schema object with properties."""
    for tool in TOOL_CATALOG:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert isinstance(schema["properties"], dict)


def test_each_input_schema_has_required_array():
    """Each tool's inputSchema declares required fields (may be empty)."""
    for tool in TOOL_CATALOG:
        schema = tool["inputSchema"]
        assert "required" in schema
        assert isinstance(schema["required"], list)


def test_tool_names_are_unique_in_catalog():
    """No duplicate tool names in the catalog."""
    names = [t["name"] for t in TOOL_CATALOG]
    assert len(names) == len(set(names))


# ----------------------------------------------------------------------
# Model-agnostic guarantee: no vendor lock-in in schemas
# ----------------------------------------------------------------------


def test_catalog_schemas_are_model_agnostic():
    """Schemas contain no vendor-specific fields (Claude, OpenAI, etc.)."""
    catalog_text = json.dumps(TOOL_CATALOG)
    vendor_terms = ["claude", "openai", "anthropic", "mistral", "llama",
                     "gemini", "gpt", "vendor", "model", "provider"]
    for term in vendor_terms:
        assert term not in catalog_text.lower(), (
            f"Tool catalog must be model-agnostic; found '{term}' in schemas"
        )


# ----------------------------------------------------------------------
# Protocol-level discovery via McpServer
# ----------------------------------------------------------------------


def test_mcp_tools_list_returns_all_five_tools(server):
    """tools/list RPC returns the full five-tool catalog from McpServer."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    result = server.handle(msg)
    assert result["jsonrpc"] == "2.0"
    assert result["id"] == 1
    tools = result["result"]["tools"]
    assert len(tools) == 5
    returned_names = [t["name"] for t in tools]
    assert set(returned_names) == set(TOOL_NAMES)


def test_mcp_tools_list_schemas_match_catalog(server):
    """The schemas returned by tools/list are identical to TOOL_CATALOG."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    result = server.handle(msg)
    returned_tools = result["result"]["tools"]
    # Order may differ; compare by name
    by_name = {t["name"]: t for t in returned_tools}
    for expected in TOOL_CATALOG:
        assert expected["name"] in by_name
        assert by_name[expected["name"]] == expected


def test_mcp_tools_list_response_includes_all_schema_fields(server):
    """Each tool in tools/list response has name, description, inputSchema."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    result = server.handle(msg)
    for tool in result["result"]["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool


# ----------------------------------------------------------------------
# Version pinning
# ----------------------------------------------------------------------


def test_catalog_version_is_pinned():
    """TOOL_CATALOG_VERSION is a pinned, non-empty string."""
    assert isinstance(TOOL_CATALOG_VERSION, str)
    assert TOOL_CATALOG_VERSION.strip()
    assert TOOL_CATALOG_VERSION != "0.0.0"


def test_mcp_initialize_reports_catalog_version(server):
    """initialize handshake includes toolCatalogVersion matching TOOL_CATALOG_VERSION."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    result = server.handle(msg)
    assert result["result"]["toolCatalogVersion"] == TOOL_CATALOG_VERSION


# ----------------------------------------------------------------------
# build_context wires a functional ToolContext (UNIT)
# ----------------------------------------------------------------------


def test_build_context_returns_tool_context_with_store_and_hub(tmp_path):
    """build_context produces a ToolContext with store, hub, agents_enabled."""
    from src.config import Config

    cfg = Config(database=str(tmp_path / "bc.db"), content_dir=str(tmp_path / "bc"))
    ctx = build_context(cfg)
    assert isinstance(ctx, ToolContext)
    assert ctx.store is not None
    assert ctx.hub is not None
    assert isinstance(ctx.agents_enabled, bool)


def test_build_context_agents_enabled_defaults_to_true(tmp_path):
    """When config has no agents_enabled, build_context defaults to True."""
    from src.config import Config

    cfg = Config(database=str(tmp_path / "bc2.db"), content_dir=str(tmp_path / "bc2"))
    ctx = build_context(cfg)
    assert ctx.agents_enabled is True


# ----------------------------------------------------------------------
# Golden-master tests: pin the full catalog shape
# ----------------------------------------------------------------------


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
        f"UPDATE_GOLDEN=1 uv run pytest tests/test_ai_mcp_catalog.py"
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


def test_tool_catalog_golden():
    """The full TOOL_CATALOG JSON shape is pinned byte-for-byte."""
    canonical = json.dumps(TOOL_CATALOG, indent=2, sort_keys=True) + "\n"
    if _maybe_update("mcp_tool_catalog.json", canonical):
        return
    _assert_golden("mcp_tool_catalog.json", canonical)


def test_tool_names_golden():
    """The ordered tuple of tool names is pinned."""
    canonical = json.dumps(list(TOOL_NAMES), indent=2) + "\n"
    if _maybe_update("mcp_tool_names.json", canonical):
        return
    _assert_golden("mcp_tool_names.json", canonical)


# ----------------------------------------------------------------------
# Integration: tools/list through McpServer matches catalog golden
# ----------------------------------------------------------------------


def test_mcp_tools_list_matches_catalog_golden(server):
    """The tools/list RPC response catalog matches the pinned golden shape."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    result = server.handle(msg)
    returned_tools = result["result"]["tools"]
    # Normalize order by name for deterministic comparison
    returned_tools_sorted = sorted(returned_tools, key=lambda t: t["name"])
    catalog_sorted = sorted(TOOL_CATALOG, key=lambda t: t["name"])
    assert returned_tools_sorted == catalog_sorted
    canonical = json.dumps(returned_tools_sorted, indent=2, sort_keys=True) + "\n"
    if _maybe_update("mcp_tools_list_response.json", canonical):
        return
    _assert_golden("mcp_tools_list_response.json", canonical)
