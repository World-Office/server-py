#!/usr/bin/env python3
"""Systematic mutation testing harness for the opencloud-docserver.

For each hand-crafted *mutant* — a realistic one-line bug injected into a
decision point of a source module — the file is temporarily mutated in
place, the focused test subset is run in a subprocess, and the outcome is
scored:

    KILLED   -> at least one test failed while the mutant was live (good:
               the suite has teeth at this decision point)
    SURVIVED -> the whole focused suite still passed with the bug live
               (a test-coverage gap: report it and add the missing case)
    DRIFT    -> the mutation recipe no longer matches the source (the
               operator list needs updating)

This is the classic mutation-testing loop: surviving mutants drive new test
cases. It upgrades the single in-code mutation smoke test into a measured,
repeatable score across the security-critical and algorithmically tricky
modules (JWT, sanitizer, CRDT, WOPI lock protocol).

Usage:
    uv run python scripts/mutation-test.py                  # all modules
    uv run python scripts/mutation-test.py --module collab  # one module
    uv run python scripts/mutation-test.py --verbose        # show pytest tail

Exit code 1 when any mutant survives (so CI / the Makefile can fail).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Mutation operators: (description, old, new, [pytest args ...])
# ---------------------------------------------------------------------------

CRYPTO_TESTS = [
    "tests/test_crypto.py",
    "tests/test_wopi_protocol_property.py",
    "-k", "roundtrip or has_iat or wrong_secret or expired or tamper or "
          "authenticate or alg_none or truncated or garbage or launch",
]

SANITIZER_TESTS = ["tests/test_sanitizer_adversarial.py"]

COLLAB_TESTS = ["tests/test_collab_modelbased.py", "tests/test_collab.py"]

PROTOCOL_TESTS = ["tests/test_wopi_protocol_property.py"]

AI_TESTS = [
    "tests/test_ai_tools.py",
    "tests/test_ai_mcp.py",
    "tests/test_ai_runner.py",
    "tests/test_ai_agent_edits.py",
]

# Each entry: module name, source path, mutants, test args. ``old`` must be
# unique inside the source file; replacements keep the file syntactically
# valid so a failure is a *semantic* kill, not a syntax-error artefact.
MUTATIONS: list[dict] = [
    {
        "module": "crypto",
        "file": "src/lib/crypto.py",
        "tests": CRYPTO_TESTS,
        "mutants": [
            ("sign with an empty secret",
             'return jwt.encode(payload, secret, algorithm="HS256")',
             'return jwt.encode(payload, "", algorithm="HS256")'),
            ("exp never fires (ttl inflated)",
             '"exp": issued + ttl,',
             '"exp": issued + ttl + 10**9,'),
            ("signature verification disabled",
             'return jwt.decode(token, secret, algorithms=["HS256"])',
             'return jwt.decode(token, secret, algorithms=["HS256"], '
             'options={"verify_signature": False})'),
            ("issued-at dropped",
             '"iat": issued,\n        "exp": issued + ttl,',
             '"exp": issued + ttl,'),
        ],
    },
    {
        "module": "sanitizer",
        "file": "src/editor/sanitize.py",
        "tests": SANITIZER_TESTS,
        "mutants": [
            ("<script> allowed through",
             'self._unsafe_tags = {"script", "iframe",',
             'self._unsafe_tags = {"iframe",'),
            ("event handlers kept",
             'if lname.startswith("on"):\n            continue',
             'if lname.startswith("on"):\n            lname = lname  # keep'),
            ("link URLs always safe",
             'if value.startswith(("https://", "http://", "mailto:", "tel:", '
             '"#", "/", "./", "../")):\n        return True\n    return False',
             'return True'),
            ("image URLs always safe",
             'if value.startswith("data:image/"):\n        return True\n'
             '    if value.startswith(("https://", "http://", "/", "./", "../")):\n'
             '        return True\n    return False',
             'return True'),
            ("raw data not escaped (entity bypass)",
             'safe = data.replace("&", "&amp;").replace("<", "&lt;")'
             '.replace(">", "&gt;")',
             'safe = data'),
            ("attribute values not escaped (breakout)",
             'value.replace("&", "&amp;")\n        .replace(\'"\', "&quot;")\n'
             '        .replace("<", "&lt;")\n        .replace(">", "&gt;")',
             'value'),
        ],
    },
    {
        "module": "collab",
        "file": "src/editor/collab.py",
        "tests": COLLAB_TESTS,
        "mutants": [
            ("sibling order not reversed (determinism broken vs reference)",
             'key=lambda iid: (self.items[iid].seq, self.items[iid].site), '
             'reverse=True',
             'key=lambda iid: (self.items[iid].seq, self.items[iid].site)'),
            ("remote delete never tombstones",
             'elif item.alive:\n                item.alive = False\n'
             '                self._text_cache = None\n                changed = True',
             'elif item.alive:\n                item.alive = item.alive  # never kill\n'
             '                self._text_cache = None\n                changed = True'),
            ("insert never invalidates the text cache",
             'self._order_cache = None\n        self._text_cache = None\n'
             '        if item.id in self._pending_deletes:',
             'self._order_cache = None\n        if item.id in self._pending_deletes:'),
            ("inserts never materialise (_add_item is a no-op)",
             'self.items[item.id] = item\n'
             '        self._children.setdefault(item.origin, []).append(item.id)',
             'pass  # mutant: inserts never materialise'),
        ],
    },
    {
        "module": "protocol",
        "file": "src/wopi/protocol.py",
        "tests": PROTOCOL_TESTS,
        "mutants": [
            ("lock mismatch reported as 200 instead of 409",
             'return WopiError(HTTP_LOCK_MISMATCH, f"Lock mismatch: '
             'expected {expected!r}, got {actual!r}")',
             'return WopiError(200, f"Lock mismatch: expected {expected!r}, '
             'got {actual!r}")'),
        ],
    },
    {
        "module": "ai",
        "file": "src/ai/tools.py",
        "tests": AI_TESTS,
        "mutants": [
            ("WOPI lock check dropped for agent writes",
             'current_lock = ctx.store.get_lock(doc_id)\n    if current_lock and lock_token != current_lock:',
             'current_lock = ctx.store.get_lock(doc_id)\n    if False and lock_token != current_lock:'),
            ("agent client_id requirement dropped (unattributed edits)",
             '    if not is_agent_client(client_id):\n        return err_result(\n            "agent_client_id_required", 400,',
             '    if False:\n        return err_result(\n            "agent_client_id_required", 400,'),
            ("unknown-document check dropped (apply_ops)",
             '    if ctx.store.get(doc_id) is None:\n        return _not_found(doc_id)\n    if not is_agent_client(client_id):',
             '    if False:\n        return _not_found(doc_id)\n    if not is_agent_client(client_id):'),
            ("per-site clock instead of global clock (interleaving regression)",
             'seq = max(crdt.lamport.values(), default=0) + 1',
             'seq = crdt.lamport.get(site, 0) + 1'),
            ("per-call op cap removed (runaway batches)",
             '    if len(ops) > MAX_OPS_PER_CALL:',
             '    if False and len(ops) > MAX_OPS_PER_CALL:'),
            ("agents_enabled kill switch ignored",
             '    if not ctx.agents_enabled:',
             '    if False:'),
        ],
    },
]


def _run(cli: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cli, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", help="only this module (crypto/sanitizer/collab/protocol)")
    parser.add_argument("--timeout", type=int, default=600, help="per-mutant timeout (s)")
    parser.add_argument("--verbose", action="store_true", help="print pytest tails")
    args = parser.parse_args()

    selected = [m for m in MUTATIONS if args.module is None or m["module"] == args.module]
    if not selected:
        print(f"unknown module {args.module!r}; known: "
              + ", ".join(m["module"] for m in MUTATIONS))
        return 2

    total = killed = drifted = survived = 0
    start = time.time()
    survived_reports: list[str] = []

    print(f"mutation-test: {len(selected)} module(s) — "
          f"{sum(len(m['mutants']) for m in selected)} mutants")
    print("-" * 78)
    for entry in selected:
        path = ROOT / entry["file"]
        original = path.read_bytes()
        try:
            for desc, old, new in entry["mutants"]:
                total += 1
                text = original.decode("utf-8")
                if old not in text:
                    print(f"  [DRIFT ] {entry['module']}: {desc} "
                          f"(recipe no longer matches source)")
                    drifted += 1
                    continue
                mutated = text.replace(old, new, 1)
                path.write_text(mutated, encoding="utf-8")
                cli = [sys.executable, "-m", "pytest", *entry["tests"],
                       "-q", "-p", "no:cacheprovider"]
                try:
                    proc = _run(cli, ROOT, args.timeout)
                    if proc.returncode != 0:
                        print(f"  [KILLED] {entry['module']}: {desc}")
                        killed += 1
                    else:
                        print(f"  [SURVIVED] {entry['module']}: {desc}  <-- GAP")
                        survived += 1
                        survived_reports.append(
                            f"  {entry['module']}: {desc}\n"
                            f"  tests: {' '.join(entry['tests'])}\n"
                            f"  {proc.stdout[-3000:]}"
                        )
                except subprocess.TimeoutExpired:
                    print(f"  [TIMEOUT] {entry['module']}: {desc}")
                    survived += 1
                    survived_reports.append(f"  {entry['module']}: {desc} (timeout)")
        finally:
            path.write_bytes(original)  # always restore the source

    elapsed = time.time() - start
    score = killed / total * 100 if total else 0
    print("-" * 78)
    print(f"mutants: {total}  killed: {killed}  survived: {survived}  "
          f"drift: {drifted}  ({elapsed:.0f}s)")
    print(f"mutation score: {score:.0f}%")
    if survived_reports and args.verbose:
        print("\n--- surviving mutants (coverage gaps) ---")
        for report in survived_reports:
            print(report)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
