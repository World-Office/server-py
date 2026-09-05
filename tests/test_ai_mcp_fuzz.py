"""FUZZ: hostile doc-ids and arguments against the MCP tool surface.

TC-E13-04 — the MCP boundary is an untrusted-input surface: any agent
framework (or hostile client) can send arbitrary JSON-RPC ``tools/call``
payloads with hostile document ids (path traversal, separators, unicode,
over-long, control characters) and arbitrary argument shapes. This file
drives that surface with Hypothesis the same way ``test_api_fuzz.py``
drives the HTTP surface, and pins the safety contract:

* the server **never raises** and never returns a malformed response —
  every ``tools/call`` answer is a well-formed JSON-RPC result whose text
  is parseable JSON;
* hostile doc ids are **typed 400 bad_request before they ever reach the
  store/hub** (no path traversal can escape the content directory, and no
  stray content file is ever created);
* fuzzed apply_ops batches can never import characters into the CRDT that
  the client did not send, never inflate the revision, and never corrupt
  the hub (a fresh legit edit still works afterwards);
* fuzzed lock/presence traffic leaves the lock plane and presence list
  structurally intact;
* garbage on the stdio transport yields parse errors, never a dead loop;
* client-grade (correctly shaped but malicious) argument values never
  produce a server-side 500.

These complement the hand-written contract tests in ``test_ai_mcp.py`` /
``test_ai_tools.py`` by generating inputs a human would not enumerate.
"""

from __future__ import annotations

import io
import json

from docx import Document
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.ai.mcp import McpServer
from src.ai.schemas import TOOL_NAMES
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir
from src.wopi.protocol import invalid_doc_id

# ----------------------------------------------------------------------
# Hostile doc ids: traversal, separators, control chars, percent-encoding,
# unicode, over-long, injection-ish (mirrors test_api_fuzz._HOSTILE_IDS).
# ----------------------------------------------------------------------

_HOSTILE_IDS = [
    "", "..", ".", "../secret", "..\\..\\secret", "a/b", "a\\b", "%2e%2e",
    "%2e%2e%2fsecret", "..%2Fsecret", "x\x00y", "αβγ-δοκιμή", "a" * 300,
    "doc id with spaces", "<script>alert(1)</script>", '"quoted"',
    "ünïcödé", "a" * 5, "normal-id", "42", "\x00\x01\x02", "a" * 130,
]

#: Any id the tools can receive — including valid-looking ones. "doc1" is
#: the only registered document, so it is filtered out to keep the
#: unknown-document expectation exact.
_DOC_ID = st.one_of(
    st.sampled_from(_HOSTILE_IDS),
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=0,
        max_size=16,
    ),
).filter(lambda s: s != "doc1")

#: Arbitrary JSON argument shapes (the MCP client can send anything).
_JSON = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(10**9), max_value=10**9),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-(10**6), max_value=10**6),
        st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=40),
    ),
    extend=lambda c: st.lists(c, max_size=3)
    | st.dictionaries(st.text(min_size=0, max_size=8), c, max_size=3),
    max_leaves=20,
)

#: apply_ops batches: arbitrary JSON trees, seeded with the op-field names
#: so raw CRDT wire ops (insert/delete) actually get built sometimes.
_OPS = st.lists(
    st.recursive(
        st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-(10**9), max_value=10**9),
            st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=40),
        ),
        extend=lambda c: st.lists(c, max_size=3)
        | st.dictionaries(
            st.sampled_from(
                ["t", "at", "end", "text", "s", "b", "n", "chars",
                 "originSite", "originSeq", "ids"]
            ),
            c,
            max_size=6,
        ),
        max_leaves=15,
    ),
    min_size=0,
    max_size=6,
)


def _docx_bytes(text: str = "FUZZ base") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_server(tmp_path):
    """Fresh store (with one registered doc "doc1") + hub + MCP server.

    Hermetic per call: Hypothesis reuses the same ``tmp_path`` across every
    generated example, so the sqlite/content files are wiped before each
    build — one example's lock/write state must never leak into the next.
    """
    db, content = str(tmp_path / "t.db"), str(tmp_path / "content")
    wipe_db(db)
    wipe_dir(content)
    store = DocumentStore(db, content)
    store.init("doc1", "fuzz.docx")
    store.put_content("doc1", _docx_bytes())
    ctx = ToolContext(store=store, hub=CollabHub())
    return McpServer(ctx), ctx


def _call(server, name: str, arguments: dict) -> dict:
    """One hostile tools/call through the MCP boundary; return its JSON wall."""
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return _payload(server.handle(msg))


def _payload(response: dict) -> dict:
    """Decode a tools/call response into the tool's JSON envelope."""
    assert "result" in response, f"expected a result, got {response!r}"
    assert isinstance(response["result"]["isError"], bool)
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def _strings_of(value: object) -> list[str]:
    """Every string reachable in an arbitrary JSON tree (used to prove the
    CRDT never invents characters the client did not send)."""
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(_strings_of(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(_strings_of(v))
    return found


def _base_args(tool: str, doc_id: str) -> dict:
    """Required-kwarg baseline per tool, so the doc-id branch — not a
    missing-argument TypeError — decides the outcome (see _expected_status)."""
    args: dict = {"doc_id": doc_id}
    if tool == "apply_ops":
        args.update({"client_id": "agent=fuzz", "ops": []})
    elif tool == "lock":
        args["action"] = "get"
    elif tool == "presence":
        args["client_id"] = "agent=fuzz"
    return args


def _expected_status(tool: str, doc_id: str) -> int:
    """Pin the current per-tool mapping of (tool, doc_id) -> http-equivalent."""
    if invalid_doc_id(doc_id):
        return 400
    if tool == "presence":
        # NOTE: existing behaviour — presence announces into the hub for any
        # valid id without requiring a registered document (it never touches
        # the store), so unknown-but-valid ids return ok, not 404.
        return 200
    return 404


# ----------------------------------------------------------------------
# 1. The whole tools/call surface survives arbitrary hostile arguments.
# ----------------------------------------------------------------------

@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(arguments=_JSON, doc_id=_DOC_ID)
def test_mcp_tools_call_surface_never_crashes_on_hostile_arguments(tmp_path, arguments, doc_id):
    """No hostile argument tree may crash the server or yield a malformed
    response: every tools/call answer is a JSON-RPC result (never a protocol
    error, never an exception), whose text is parseable JSON with a typed
    ok/error envelope."""
    server, _ctx = _make_server(tmp_path)
    if isinstance(arguments, dict):
        args = {**arguments, "doc_id": doc_id}
    else:
        # non-object arguments are legal for a hostile client to send — the
        # boundary must reject them as a typed bad_request, not crash
        args = {"doc_id": doc_id, "extra": arguments}
    for name in TOOL_NAMES:
        msg = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
        resp = server.handle(msg)  # must not raise
        assert resp is not None, f"tools/call must never be swallowed for {name}"
        assert resp["jsonrpc"] == "2.0" and resp["id"] == 7
        payload = _payload(resp)
        assert isinstance(payload["ok"], bool)
        if not payload["ok"]:
            assert isinstance(payload["error"], str)
            assert payload.get("status") in (400, 403, 404, 409, 413, 500)
            assert "Traceback" not in json.dumps(payload)


# ----------------------------------------------------------------------
# 2. Hostile doc ids are typed bad_request on every tool — before any
#    store/hub access, so traversal can never escape the content dir.
# ----------------------------------------------------------------------

@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(doc_id=_DOC_ID)
def test_hostile_doc_ids_are_typed_bad_request_on_every_tool(tmp_path, doc_id):
    """Pins the doc-id contract for all five tools: invalid ids (traversal,
    separators, control chars, over-long) are rejected with the same typed
    400 bad_request the WOPI surface uses, valid-but-unknown ids are the
    standard 404 (except presence, which never queries the store). A
    traversal that reached the store would create/lookup a file next to
    doc1.bin — assert the content directory is byte-for-byte untouched."""
    server, ctx = _make_server(tmp_path)
    content_dir = ctx.store._content_dir
    before = sorted(p.name for p in content_dir.iterdir())
    for name in TOOL_NAMES:
        payload = _call(server, name, _base_args(name, doc_id))
        expected = _expected_status(name, doc_id)
        assert payload["ok"] is (expected == 200), (
            f"{name}({doc_id!r}): expected ok={expected == 200}, got {payload}"
        )
        if expected == 400:
            assert payload["error"] == "bad_request"
            assert payload["status"] == 400
        elif expected == 404:
            assert payload["error"] == "not_found"
            assert payload["status"] == 404
    after = sorted(p.name for p in content_dir.iterdir())
    assert after == before, f"hostile id {doc_id!r} created stray content files: {after}"


# ----------------------------------------------------------------------
# 3. Fuzzed apply_ops batches never corrupt the hub: no invented
#    characters, no runaway revision, and a legit edit still works after.
# ----------------------------------------------------------------------

@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(ops=_OPS)
def test_apply_ops_hostile_batches_keep_hub_consistent(tmp_path, ops):
    """Fuzzed op batches (including raw CRDT wire ops with hostile fields)
    must leave the document consistent: the resulting text contains only
    characters the client actually sent, the revision grows by exactly the
    number of applied ops (never more), and a subsequent legitimate agent
    edit still lands. No batch may produce a server-side 500."""
    server, ctx = _make_server(tmp_path)
    doc_id = "doc1"
    sent_chars: set[str] = set("FUZZ base")  # the seeded collaboration text
    for s in _strings_of(ops):
        sent_chars.update(s)

    # Seed the hub exactly like _apply_through_hub would, then capture the
    # baseline revision (a bare hub.state() would seed with empty text).
    ctx.hub.ensure(doc_id, "FUZZ base")
    rev0 = ctx.hub.state(doc_id)["rev"]
    payload = _call(
        server, "apply_ops",
        {"doc_id": doc_id, "client_id": "agent=fuzz", "ops": ops},
    )
    assert payload.get("status") != 500, f"server 500 on ops {ops!r}: {payload}"
    if payload["ok"]:
        text = payload["text"]
        assert isinstance(text, str)
        # hub and the reply must agree on the exact same converged state
        assert ctx.hub.state(doc_id)["text"] == text
        # revision arithmetic: each applied op bumps rev by exactly one
        assert payload["rev"] == rev0 + payload["applied_count"]
        assert payload["applied_count"] <= len(ops)
        # the CRDT never invents characters the client did not send
        assert set(text) <= sent_chars, (
            f"text {text!r} contains characters never sent by the client"
        )
    else:
        # typed client error (e.g. empty/empty-list ops) — hub untouched
        assert payload["status"] in (400, 404, 409, 413)
        assert ctx.hub.state(doc_id)["text"] == "FUZZ base"

    # the hub still works for a plain legit edit afterwards
    after = _call(
        server, "apply_ops",
        {"doc_id": doc_id, "client_id": "agent=fuzz",
         "ops": [{"t": "ins", "at": 0, "text": "!"}]},
    )
    assert after["ok"] is True and after["text"].startswith("!")


# ----------------------------------------------------------------------
# 4. Fuzzed lock + presence traffic leaves state structurally intact.
# ----------------------------------------------------------------------

@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(action=st.text(min_size=0, max_size=12), token=_JSON, user=_JSON)
def test_lock_fuzz_never_corrupts_the_lock_plane(tmp_path, action, token, user):
    """Lock fuzzing: hostile action/token/user combinations may succeed,
    refresh, conflict (409) or be rejected (400) — but they must never raise,
    never 500, and can never leave the lock plane holding a value the client
    did not hand to a successful lock/refresh call."""
    server, ctx = _make_server(tmp_path)
    tokens_passed: set[str] = set()
    if isinstance(token, str) and action in ("lock", "refresh"):
        tokens_passed.add(token)

    payload = _call(
        server, "lock",
        {"doc_id": "doc1", "action": action, "token": token, "user": user},
    )
    assert payload.get("status") != 500, f"lock fuzz -> 500: {payload}"
    lock_now = ctx.store.get_lock("doc1")
    assert isinstance(lock_now, str)
    # either unlocked, or holding a token a successful lock/refresh wrote
    assert lock_now == "" or lock_now in tokens_passed, (
        f"lock plane corrupted to {lock_now!r} by action={action!r} token={token!r}"
    )
    # a plain fresh lock must still work — the plane is not bricked
    ctx.store.set_lock("doc1", "fresh-token")
    assert ctx.store.get_lock("doc1") == "fresh-token"


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    client_id=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=16),
    user=_JSON,
    cursor=_JSON,
    leave=st.one_of(st.booleans(), st.integers(min_value=-2, max_value=2), st.none()),
)
def test_presence_fuzz_keeps_presence_list_structurally_valid(tmp_path, client_id, user, cursor, leave):
    """Presence fuzzing: only genuine agent client ids may ever appear on the
    list, every entry keeps its structural shape, cursors stay integers, and
    nothing raises or 500s. (The ``user`` field is stored verbatim — see NOTE.)"""
    server, ctx = _make_server(tmp_path)
    payload = _call(
        server, "presence",
        {"doc_id": "doc1", "client_id": client_id, "user": user,
         "cursor": cursor, "leave": leave},
    )
    assert payload.get("status") != 500, f"presence fuzz -> 500: {payload}"
    agent_prefix = isinstance(client_id, str) and client_id.startswith("agent=")
    for entry in ctx.hub.clients("doc1"):
        # structural shape of a presence entry is immutable
        assert set(entry) >= {"client", "user", "cursor", "updated", "agent"}
        # NOTE: existing behaviour — ``user`` is stored verbatim (it may be a
        # non-str when fuzzed); only the client id is validated.
        assert entry["client"] in ({client_id} if agent_prefix else set()), (
            f"non-agent client {entry['client']!r} leaked onto the presence list"
        )
        assert entry["agent"] is True
        assert isinstance(entry["cursor"], int)


# ----------------------------------------------------------------------
# 5. Transport-level fuzz: garbage on the stdio wire is a parse error,
#    never a dead loop.
# ----------------------------------------------------------------------

@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(lines=st.lists(st.text(min_size=0, max_size=80), min_size=0, max_size=8))
def test_transport_garbage_lines_never_kill_the_loop(tmp_path, lines):
    """Arbitrary text lines on the stdio transport (binary garbage, random
    JSON, half-finished messages) produce typed parse errors and the loop
    keeps serving: a valid ping placed after the garbage always gets its
    answer, so a hostile client cannot wedge the server."""
    server, _ctx = _make_server(tmp_path)
    input_lines = lines + [json.dumps({"jsonrpc": "2.0", "id": 999, "method": "ping"})]
    out = io.StringIO()
    server.serve(io.StringIO("\n".join(input_lines) + "\n"), out)  # must not raise
    outputs = [json.loads(line) for line in out.getvalue().splitlines()]
    # responses come back in order -> the trailing ping is answered last
    assert outputs[-1]["id"] == 999
    assert outputs[-1]["result"] == {}
    for resp in outputs:
        assert resp["jsonrpc"] == "2.0"
        if "error" in resp:
            assert resp["error"]["code"] in (-32700, -32600, -32601, -32602)


# ----------------------------------------------------------------------
# 6. Correctly-shaped but malicious argument values are client bugs
#    (400/409/413), never server defects (500).
# ----------------------------------------------------------------------

#: Per-tool arguments that are correctly typed (right kwarg names and value
#: kinds) but intentionally hostile in value — the exact class of input an
#: agent framework would translate from a prompt-injected instruction.
_TYPED_HOSTILE_ARGS: dict[str, list[dict]] = {
    "read_doc": [
        {"doc_id": "doc1", "ops_tail": -5},
        {"doc_id": "doc1", "ops_tail": 10**12},
        {"doc_id": "doc1", "include_content": "yes"},
        {"doc_id": "doc1", "ops_tail": 0, "include_content": 1},
    ],
    "apply_ops": [
        {"doc_id": "doc1", "client_id": "agent=x", "ops": []},
        {"doc_id": "doc1", "client_id": "agent=x", "ops": [{"t": "ins", "at": -(10**9), "text": "a" * 300}]},
        {"doc_id": "doc1", "client_id": "agent=x", "ops": [{"t": "del", "at": 0, "end": 10**9}]},
        {"doc_id": "doc1", "client_id": "agent=x", "ops": [{"t": "delete", "s": "x", "ids": []}]},
        {"doc_id": "doc1", "client_id": "agent=x", "ops": [{"t": "insert", "s": "agent=x", "b": 10**12, "chars": "Ψ" * 5, "originSite": "r", "originSeq": -7}]},
        {"doc_id": "doc1", "client_id": "agent=x", "ops": [{"t": "insert", "s": "agent=x", "b": 0, "chars": "", "originSite": "r", "originSeq": 0}]},
    ],
    "get_versions": [
        {"doc_id": "doc1"},
    ],
    "lock": [
        {"doc_id": "doc1", "action": "lock", "token": "a" * 500},
        {"doc_id": "doc1", "action": "refresh", "token": "\x00tok\x00"},
        {"doc_id": "doc1", "action": "get", "token": None},
        {"doc_id": "doc1", "action": "unlock", "token": 9},
        {"doc_id": "doc1", "action": "lock", "token": "valid-tok", "user": "x" * 200},
    ],
    "presence": [
        {"doc_id": "doc1", "client_id": "agent=x", "cursor": -100},
        {"doc_id": "doc1", "client_id": "agent=x", "cursor": 10**12},
        {"doc_id": "doc1", "client_id": "agent=x", "user": "", "leave": 0},
        {"doc_id": "doc1", "client_id": "agent=x", "user": "\x00", "cursor": 0},
    ],
}


def test_well_typed_hostile_args_are_never_server_500(tmp_path):
    """Correctly-shaped but hostile argument values are client errors — the
    boundary must map them to the typed 400/404/409/413 envelope (or apply
    them safely) and must never answer with a server-side 500."""
    server, _ctx = _make_server(tmp_path)
    for name, arg_sets in _TYPED_HOSTILE_ARGS.items():
        for args in arg_sets:
            payload = _call(server, name, args)  # must not raise
            if not payload["ok"]:
                assert payload.get("status") != 500, (
                    f"{name}{args!r} -> server 500: {payload}"
                )
                assert payload["status"] in (400, 404, 409, 413)
