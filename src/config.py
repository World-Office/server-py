"""Configuration loader for opencloud-docserver.

Loads `config.toml` and merges environment overrides (DOCSERVER_ prefix).
Environment variables always win over the file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration."""

    port: int = 8000
    host: str = "0.0.0.0"
    jwt_secret: str = "change-me-32-chars-minimum"
    jwt_ttl: int = 3600
    database: str = "data/docserver.db"
    content_dir: str = "data/documents"
    public_url: str = "http://localhost:8000"
    cors_origins: str = "*"
    wopi_host: str = ""
    data_dir: str = "data"
    agents_enabled: bool = True

    @property
    def document_dir(self) -> str:
        """Directory where document import/export staging happens."""
        return self.data_dir

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list; `*` maps to ["*"]."""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def _load_toml(path: str | Path) -> dict:
    """Read and parse a TOML file, returning {} if missing."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return data or {}


def load_config(path: str | Path = "config.toml") -> Config:
    """Load config from TOML file with DOCSERVER_* env overrides."""
    raw = _load_toml(path)

    merged: dict = {
        "port": _first("DOCSERVER_PORT", _dig(raw, "server", "port"), 8000, int),
        "host": _first_str("DOCSERVER_HOST", _dig(raw, "server", "host"), "0.0.0.0"),
        "jwt_secret": _first_str(
            "DOCSERVER_JWT_SECRET", _dig(raw, "security", "jwt_secret"), "change-me"
        ),
        "jwt_ttl": _first("DOCSERVER_JWT_TTL", _dig(raw, "security", "jwt_ttl"), 3600, int),
        "database": _first_str("DOCSERVER_DATABASE", _dig(raw, "storage", "database"), "data/docserver.db"),
        "content_dir": _first_str("DOCSERVER_CONTENT_DIR", _dig(raw, "storage", "content_dir"), "data/documents"),
        "public_url": _first_str("DOCSERVER_PUBLIC_URL", _dig(raw, "app", "public_url"), "http://localhost:8000"),
        "cors_origins": _first_str("DOCSERVER_CORS_ORIGINS", _dig(raw, "app", "cors_origins"), "*"),
        "wopi_host": _first_str(
            "WOPI_HOST", _dig(raw, "app", "wopi_host"), ""
        ),
    }
    merged["data_dir"] = _first_str(
        "DOCSERVER_DATA_DIR",
        _dig(raw, "storage", "data_dir"),
        str(Path(merged["database"]).parent),
    )
    merged["agents_enabled"] = _first_str(
        "DOCSERVER_AGENTS", _dig(raw, "ai", "enabled"), "true"
    ).lower() not in ("0", "false", "no")
    return Config(**merged)


def _dig(d: dict, *keys: str) -> object:
    """Fetch a nested dict value by successive keys, None if absent."""
    cur: object = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _first(env: str, file_val: object, default: object, typ: type) -> object:
    """Return env value > file value > default, coerced with typ."""
    env_val = os.environ.get(env)
    if env_val is not None:
        return typ(env_val)
    if file_val is not None:
        return typ(file_val)
    return default


def _first_str(env: str, file_val: object, default: str) -> str:
    """String variant of _first."""
    env_val = os.environ.get(env)
    if env_val is not None:
        return env_val
    if file_val is not None:
        return str(file_val)
    return default
