"""Tests for configuration precedence: env overrides toml overrides defaults.

This suite verifies that load_config() correctly applies precedence rules:
1. Environment variables (DOCSERVER_*) win over TOML file values
2. TOML file values win over built-in defaults
3. Typed parsing works correctly (int, str, bool coercion)
4. Nested TOML access via _dig works as expected
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from src.config import Config, _dig, _first, _first_str, load_config


# =============================================================================
# Helper to write temporary TOML files for testing
# =============================================================================

def _write_toml(tmp_path, content: str) -> Path:
    """Write a TOML string to a temporary file and return its path."""
    toml_file = tmp_path / "test_config.toml"
    toml_file.write_text(content)
    return toml_file


# =============================================================================
# 1. Precedence: defaults < toml < env
# =============================================================================

def test_defaults_used_when_no_toml_and_no_env(tmp_path, monkeypatch):
    """Defaults are used when no TOML file exists and no env vars are set."""
    # Ensure no env vars influence the test
    for key in {
        "DOCSERVER_PORT",
        "DOCSERVER_HOST",
        "DOCSERVER_JWT_SECRET",
        "DOCSERVER_JWT_TTL",
        "DOCSERVER_DATABASE",
        "DOCSERVER_CONTENT_DIR",
        "DOCSERVER_PUBLIC_URL",
        "DOCSERVER_CORS_ORIGINS",
        "DOCSERVER_DATA_DIR",
        "DOCSERVER_AGENTS",
    }:
        monkeypatch.delenv(key, raising=False)

    nonexistent_toml = tmp_path / "does_not_exist.toml"
    cfg = load_config(nonexistent_toml)

    assert cfg.port == 8000
    assert cfg.host == "0.0.0.0"
    assert cfg.jwt_secret == "change-me"
    assert cfg.jwt_ttl == 3600
    assert cfg.database == "data/docserver.db"
    assert cfg.content_dir == "data/documents"
    assert cfg.public_url == "http://localhost:8000"
    assert cfg.cors_origins == "*"
    assert cfg.data_dir == "data"
    assert cfg.agents_enabled is True


def test_toml_overrides_defaults(tmp_path, monkeypatch):
    """TOML file values override built-in defaults."""
    for key in {
        "DOCSERVER_PORT",
        "DOCSERVER_HOST",
        "DOCSERVER_JWT_SECRET",
        "DOCSERVER_JWT_TTL",
        "DOCSERVER_DATABASE",
        "DOCSERVER_CONTENT_DIR",
        "DOCSERVER_PUBLIC_URL",
        "DOCSERVER_CORS_ORIGINS",
        "DOCSERVER_DATA_DIR",
        "DOCSERVER_AGENTS",
    }:
        monkeypatch.delenv(key, raising=False)

    toml_content = """
[server]
port = 9000
host = "127.0.0.1"

[security]
jwt_secret = "toml-secret-42"
jwt_ttl = 7200

[storage]
database = "custom/db.sqlite"
content_dir = "custom/docs"

[app]
public_url = "https://example.com:9000"
cors_origins = "https://example.com"

[ai]
enabled = false
"""
    toml_file = _write_toml(tmp_path, toml_content)
    cfg = load_config(toml_file)

    assert cfg.port == 9000
    assert cfg.host == "127.0.0.1"
    assert cfg.jwt_secret == "toml-secret-42"
    assert cfg.jwt_ttl == 7200
    assert cfg.database == "custom/db.sqlite"
    assert cfg.content_dir == "custom/docs"
    assert cfg.public_url == "https://example.com:9000"
    assert cfg.cors_origins == "https://example.com"
    assert cfg.agents_enabled is False


def test_env_overrides_toml_and_defaults(tmp_path, monkeypatch):
    """Environment variables override both TOML file and defaults."""
    toml_content = """
[server]
port = 9000
host = "127.0.0.1"
"""
    toml_file = _write_toml(tmp_path, toml_content)

    monkeypatch.setenv("DOCSERVER_PORT", "9999")
    monkeypatch.setenv("DOCSERVER_HOST", "0.0.0.0")
    # Set other env vars to defaults to avoid interference
    monkeypatch.setenv("DOCSERVER_JWT_SECRET", "change-me-32-chars-minimum")
    monkeypatch.setenv("DOCSERVER_JWT_TTL", "3600")
    monkeypatch.setenv("DOCSERVER_DATABASE", "data/docserver.db")
    monkeypatch.setenv("DOCSERVER_CONTENT_DIR", "data/documents")
    monkeypatch.setenv("DOCSERVER_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setenv("DOCSERVER_CORS_ORIGINS", "*")

    cfg = load_config(toml_file)

    # Env wins over TOML
    assert cfg.port == 9999
    assert cfg.host == "0.0.0.0"
    # TOML fields not in env should still be from TOML
    # But we set all env vars, so check defaults that weren't in TOML


def test_env_overrides_env_toml_overrides_toml_overrides_defaults(tmp_path, monkeypatch):
    """Full precedence chain: env > toml > defaults.
    
    This test sets up a three-way contest and verifies the winner at each level.
    """
    toml_content = """
[server]
port = 9000

[security]
jwt_ttl = 7200
"""
    toml_file = _write_toml(tmp_path, toml_content)

    # Set env for port (wins over TOML), but not for jwt_ttl (TOML wins over default)
    monkeypatch.setenv("DOCSERVER_PORT", "9999")
    # Clear all other env vars
    for key in {
        "DOCSERVER_HOST",
        "DOCSERVER_JWT_SECRET",
        "DOCSERVER_JWT_TTL",
        "DOCSERVER_DATABASE",
        "DOCSERVER_CONTENT_DIR",
        "DOCSERVER_PUBLIC_URL",
        "DOCSERVER_CORS_ORIGINS",
        "DOCSERVER_DATA_DIR",
        "DOCSERVER_AGENTS",
    }:
        monkeypatch.delenv(key, raising=False)

    cfg = load_config(toml_file)

    # Env wins
    assert cfg.port == 9999
    # TOML wins over default
    assert cfg.jwt_ttl == 7200
    # Default for host
    assert cfg.host == "0.0.0.0"


# =============================================================================
# 2. Typed parsing
# =============================================================================

def test_int_parsing_from_env(tmp_path, monkeypatch):
    """Integer environment variables are correctly parsed as int."""
    monkeypatch.delenv("DOCSERVER_PORT", raising=False)
    monkeypatch.setenv("DOCSERVER_PORT", "8888")

    nonexistent_toml = tmp_path / "no.toml"
    cfg = load_config(nonexistent_toml)

    assert cfg.port == 8888
    assert isinstance(cfg.port, int)


def test_int_parsing_from_toml(tmp_path, monkeypatch):
    """Integer TOML values are correctly parsed as int."""
    for key in {
        "DOCSERVER_PORT",
        "DOCSERVER_JWT_TTL",
    }:
        monkeypatch.delenv(key, raising=False)

    toml_content = """
[server]
port = 7777

[security]
jwt_ttl = 1800
"""
    toml_file = _write_toml(tmp_path, toml_content)
    cfg = load_config(toml_file)

    assert cfg.port == 7777
    assert isinstance(cfg.port, int)
    assert cfg.jwt_ttl == 1800
    assert isinstance(cfg.jwt_ttl, int)


def test_str_parsing_from_env(tmp_path, monkeypatch):
    """String environment variables are correctly parsed as str."""
    monkeypatch.delenv("DOCSERVER_HOST", raising=False)
    monkeypatch.setenv("DOCSERVER_HOST", "192.168.1.1")

    nonexistent_toml = tmp_path / "no.toml"
    cfg = load_config(nonexistent_toml)

    assert cfg.host == "192.168.1.1"
    assert isinstance(cfg.host, str)


def test_bool_parsing_agents_enabled_from_env(tmp_path, monkeypatch):
    """Boolean string from env is correctly parsed for agents_enabled."""
    for key in {
        "DOCSERVER_PORT",
        "DOCSERVER_HOST",
        "DOCSERVER_JWT_SECRET",
        "DOCSERVER_JWT_TTL",
        "DOCSERVER_DATABASE",
        "DOCSERVER_CONTENT_DIR",
        "DOCSERVER_PUBLIC_URL",
        "DOCSERVER_CORS_ORIGINS",
        "DOCSERVER_DATA_DIR",
        "DOCSERVER_AGENTS",
    }:
        monkeypatch.delenv(key, raising=False)

    # Test various false values
    nonexistent_toml = tmp_path / "does_not_exist.toml"
    for false_val in ("0", "false", "False", "FALSE", "no", "NO"):
        monkeypatch.setenv("DOCSERVER_AGENTS", false_val)
        cfg = load_config(nonexistent_toml)
        assert cfg.agents_enabled is False, f"Expected False for env value: {false_val}"

    # Test true values
    for true_val in ("1", "true", "True", "TRUE", "yes", "YES", "random-string"):
        monkeypatch.setenv("DOCSERVER_AGENTS", true_val)
        cfg = load_config(nonexistent_toml)
        assert cfg.agents_enabled is True, f"Expected True for env value: {true_val}"


# =============================================================================
# 3. Nested _dig access
# =============================================================================

def test_dig_returns_nested_values():
    """_dig correctly retrieves nested dictionary values."""
    data = {
        "server": {"port": 8080, "host": "localhost"},
        "security": {"jwt_secret": "secret-key"},
    }

    assert _dig(data, "server", "port") == 8080
    assert _dig(data, "server", "host") == "localhost"
    assert _dig(data, "security", "jwt_secret") == "secret-key"


def test_dig_returns_none_for_missing_keys():
    """_dig returns None for missing keys."""
    data = {"server": {"port": 8080}}

    assert _dig(data, "server", "missing") is None
    assert _dig(data, "missing", "key") is None
    assert _dig(data, "server", "port", "extra") is None
    assert _dig({}, "any") is None


def test_dig_returns_none_for_non_dict_intermediate():
    """_dig returns None when intermediate value is not a dict."""
    data = {"server": "not-a-dict", "port": 8080}

    assert _dig(data, "server", "port") is None
    assert _dig(data, "port", "nested") is None


def test_dig_single_key():
    """_dig works with a single key."""
    data = {"port": 8080}
    assert _dig(data, "port") == 8080


# =============================================================================
# 4. Precedence with nested TOML and env
# =============================================================================

def test_nested_toml_overrides_defaults(tmp_path, monkeypatch):
    """Nested TOML structure correctly overrides defaults."""
    for key in {
        "DOCSERVER_PORT",
        "DOCSERVER_HOST",
        "DOCSERVER_JWT_SECRET",
        "DOCSERVER_JWT_TTL",
        "DOCSERVER_DATABASE",
        "DOCSERVER_CONTENT_DIR",
        "DOCSERVER_PUBLIC_URL",
        "DOCSERVER_CORS_ORIGINS",
        "DOCSERVER_DATA_DIR",
        "DOCSERVER_AGENTS",
    }:
        monkeypatch.delenv(key, raising=False)

    toml_content = """
[server]
port = 5000
host = "10.0.0.1"

[security]
jwt_secret = "nested-toml-secret"
jwt_ttl = 14400

[storage]
database = "nested/data/db.sqlite"
content_dir = "nested/data/docs"

[app]
public_url = "https://nested.example.com"
cors_origins = "https://a.com,https://b.com"
"""
    toml_file = _write_toml(tmp_path, toml_content)
    cfg = load_config(toml_file)

    assert cfg.port == 5000
    assert cfg.host == "10.0.0.1"
    assert cfg.jwt_secret == "nested-toml-secret"
    assert cfg.jwt_ttl == 14400
    assert cfg.database == "nested/data/db.sqlite"
    assert cfg.content_dir == "nested/data/docs"
    assert cfg.public_url == "https://nested.example.com"
    assert cfg.cors_origins == "https://a.com,https://b.com"


# =============================================================================
# 5. _first and _first_str helper functions
# =============================================================================

def test_first_env_wins():
    """_first returns env value when present, regardless of other values."""
    with patch.dict(os.environ, {"TEST_VAR": "env_value"}):
        result = _first("TEST_VAR", "file_value", "default", str)
        assert result == "env_value"


def test_first_file_wins_over_default():
    """_first returns file value when env is absent and file value exists."""
    with patch.dict(os.environ, {}, clear=True):
        # Remove TEST_VAR from env
        os.environ.pop("TEST_VAR", None)
        result = _first("TEST_VAR", "file_value", "default", str)
        assert result == "file_value"


def test_first_default_wins():
    """_first returns default when both env and file value are absent."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_VAR", None)
        result = _first("TEST_VAR", None, "default", str)
        assert result == "default"


def test_first_type_coercion():
    """_first applies type coercion to the winning value."""
    with patch.dict(os.environ, {"TEST_VAR": "42"}):
        result = _first("TEST_VAR", None, 0, int)
        assert result == 42
        assert isinstance(result, int)


def test_first_str_env_wins():
    """_first_str returns env value when present."""
    with patch.dict(os.environ, {"TEST_VAR": "env_string"}):
        result = _first_str("TEST_VAR", "file_string", "default")
        assert result == "env_string"


def test_first_str_file_wins():
    """_first_str returns file value when env is absent."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_VAR", None)
        result = _first_str("TEST_VAR", "file_string", "default")
        assert result == "file_string"


def test_first_str_default_wins():
    """_first_str returns default when both env and file value are absent."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_VAR", None)
        result = _first_str("TEST_VAR", None, "default_string")
        assert result == "default_string"


# =============================================================================
# 6. Config dataclass properties
# =============================================================================

def test_document_dir_property():
    """Config.document_dir returns data_dir."""
    cfg = Config(data_dir="/custom/data")
    assert cfg.document_dir == "/custom/data"


def test_cors_origin_list_wildcard():
    """Config.cors_origin_list returns [\"*\"] when cors_origins is \"*\"."""
    cfg = Config(cors_origins="*")
    assert cfg.cors_origin_list == ["*"]


def test_cors_origin_list_multiple():
    """Config.cors_origin_list splits comma-separated origins."""
    cfg = Config(cors_origins="https://a.com, https://b.com , https://c.com")
    assert cfg.cors_origin_list == ["https://a.com", "https://b.com", "https://c.com"]


def test_cors_origin_list_empty():
    """Config.cors_origin_list handles empty/whitespace strings."""
    cfg = Config(cors_origins="")
    assert cfg.cors_origin_list == []


# =============================================================================
# 7. Property-based tests with Hypothesis
# =============================================================================

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    port=st.integers(min_value=1, max_value=65535),
)
def test_property_int_port_from_env(port, monkeypatch):
    """Property: any valid integer port can be set via DOCSERVER_PORT."""
    with patch.dict(os.environ, {"DOCSERVER_PORT": str(port)}, clear=False):
        # Clear other env vars that might affect the test
        for key in {
            "DOCSERVER_HOST",
            "DOCSERVER_JWT_SECRET",
            "DOCSERVER_JWT_TTL",
            "DOCSERVER_DATABASE",
            "DOCSERVER_CONTENT_DIR",
            "DOCSERVER_PUBLIC_URL",
            "DOCSERVER_CORS_ORIGINS",
            "DOCSERVER_DATA_DIR",
            "DOCSERVER_AGENTS",
        }:
            monkeypatch.delenv(key, raising=False)
        
        cfg = load_config("/tmp/nonexistent.toml")
        assert cfg.port == port
