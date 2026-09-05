"""opencloud-docserver CLI — small operational commands.

Stoic rule: the server does one job; the CLI helps you operate it.

    python -m src.cli seed sample.docx "My Sample"
    python -m src.cli list
    python -m src.cli health
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from .config import load_config
from .lib.store import DocumentStore

BASE_URL = "http://127.0.0.1:8000"


def _store_from_config():
    cfg = load_config()
    return DocumentStore(cfg.database, cfg.content_dir)


def cmd_seed(args: argparse.Namespace) -> None:
    """Register a local file (e.g. sample.docx) into the store."""
    from docx import Document  # only needed for seeding

    path = args.path
    data = path.read_bytes()

    # Validate: must be a DOCX (python-docx can open it)
    try:
        Document(open(path, "rb"))
    except Exception as exc:
        sys.exit(f"error: {path} is not a readable DOCX ({exc})")

    store = _store_from_config()
    doc_id = args.doc_id or path.name
    store.init(doc_id, path.name)
    store.put_content(doc_id, data)
    print(f"seeded {doc_id} ({len(data)} bytes)")


def cmd_list(args: argparse.Namespace) -> None:
    store = _store_from_config()
    docs = store.list()
    if not docs:
        print("no documents")
        return
    for d in docs:
        lock = " [locked]" if d["lock_token"] else ""
        print(f"{d['id']:40} {d['size']:>8} B{lock}")


def cmd_health(args: argparse.Namespace) -> None:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as resp:
            print(json.dumps(json.loads(resp.read()), indent=2))
    except Exception as exc:
        sys.exit(f"error: server not reachable ({exc})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="opencloud-docserver", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="register a local DOCX into the store")
    p_seed.add_argument("path", type=argparse.FileType("rb"))
    p_seed.add_argument("--doc-id", default="")
    p_seed.set_defaults(func=cmd_seed)

    p_list = sub.add_parser("list", help="list stored documents")
    p_list.set_defaults(func=cmd_list)

    p_health = sub.add_parser("health", help="check the running server")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
