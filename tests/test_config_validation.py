"""Unit tests for config validation in ``src.config``.

Paradigm: **Unit tests**. Coverage:

1. **Defaults** — ``load_config()`` returns expected defaults when no file or
   env vars are present.
2. **File loading** — Values from ``config.toml`` are correctly parsed and applied.
3. **Env precedence** — ``DOCSERVER_*`` environment variables override both
   file values and defaults.
4. **Typed errors** — Invalid types in environment variables (e.g., non-integer
   port) trigger the expected exception.
5. **Derived config** — ``data_dir`` is correctly derived from ``database``
   path if not explicitly provided.
"""

from __future__ import annotations

import os
import pytest

from src.config import Config, load_config


def test_load_config_defaults(monkeypatch, tmp_path):
    """``load_config()`` returns system defaults when no config file exists
    and no environment variables are set."""
    # Ensure no env vars interfere
    for key in os.environ:
        if key.startswith("DOCSERVER_") or key == "WOPI_HOST":
            monkeypatch.delenv(key)

    # Use a path that doesn't exist
    config = load_config(path=tmp_path / "nonexistent.toml")

    assert config.port == 8000
    assert config.host == "0.0.0.0"
    assert config.jwt_secret == "change-me"
    assert config.agents_enabled is True
    # Default database is 'data/docserver.db', so data_dir should be 'data'
    assert config.data_dir == "data"


def test_load_config_from_file(monkeypatch, tmp_path):
    """Values in ``config.toml`` override system defaults."""
    for key in os.environ:
        if key.startswith("DOCSERVER_") or key == "WOPI_HOST":
            monkeypatch.delenv(key)

    conf_file = tmp_path / "config.toml"
    conf_file.write_text(
        '[server]\nport = 9000\nhost = "127.0.0.1"\n'
        '[security]\njwt_secret = "file-secret"\n'
        '[ai]\nenabled = false\n'
    )

    config = load_config(path=conf_file)

    assert config.port == 9000
    assert config.host == "127.0.0.1"
    assert config.jwt_secret == "file-secret"
    assert config.agents_enabled is False


def test_load_config_env_precedence(monkeypatch, tmp_path):
    """Environment variables override both file values and defaults."""
    conf_file = tmp_path / "config.toml"
    conf_file.write_text(
        '[server]\nport = 9000\n'
        '[security]\njwt_secret = "file-secret"\n'
    )

    monkeypatch.setenv("DOCSERVER_PORT", "7000")
    monkeypatch.setenv("DOCSERVER_JWT_SECRET", "env-secret")
    # Port in env (7000) should win over file (9000)
    # jwt_secret in env (env-secret) should win over file (file-secret)

    config = load_config(path=conf_file)

    assert config.port == 7000
    assert config.jwt_secret == "env-secret"


def test_load_config_typed_errors(monkeypatch, tmp_path):
    """Providing a non-integer value for an int field in env vars raises ValueError."""
    monkeypatch.setenv("DOCSERVER_PORT", "not-a-number")

    with pytest.raises(ValueError):
        load_config(path=tmp_path / "empty.toml")


def test_load_config_derived_data_dir(monkeypatch, tmp_path):
    """``data_dir`` defaults to the parent directory of ``database``."""
    for key in os.environ:
        if key.startswith("DOCSERVER_") or key == "WOPI_HOST":
            monkeypatch.delenv(key)

    # Case 1: Explicit database path in env
    monkeypatch.setenv("DOCSERVER_DATABASE", "/tmp/custom/my.db")
    config = load_config(path=tmp_path / "empty.toml")
    assert config.data_dir == "/tmp/custom"

    # Case 2: Explicit data_dir overrides derived one
    monkeypatch.setenv("DOCSERVER_DATA_DIR", "/tmp/override")
    config = load_config(path=tmp_path / "empty.toml")
    assert config.data_dir == "/tmp/override"


def test_config_cors_origin_list():
    """``cors_origin_list`` correctly parses comma-separated strings and handles '*'."""
    # Test wildcard
    cfg1 = Config(cors_origins="*")
    assert cfg1.cors_origin_list == ["*"]

    # Test list
    cfg2 = Config(cors_origins=" http://a.com, http://b.com ,")
    assert cfg2.cors_origin_list == ["http://a.com", "http://b.com"]

    # Test empty/whitespace
    cfg3 = Config(cors_origins="  ")
    assert cfg3.cors_origin_list == []
